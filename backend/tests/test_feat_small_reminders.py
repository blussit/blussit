"""SMALL-2 (2026-10-07): renewal-aware plan notes. The pass-ending note
("… or renew to keep going") and the pass-ended note ("Renew any time")
used to reach an old pass whose renewal was already paid (a custom-plan
renewal: `renewed_by_custom_plan_id` / `renewed_by_subscription_id`). Such a
pass now hears "Your plan continues on <start> — next period already
paid" instead, and never through the renew templates. A pass without a
renewal keeps the old notes; a renewal refunded since no longer counts.
Razorpay is stubbed; local Mongo only."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app import main
from app.schemas.custom_plan_schema import CustomPlanRenewRequest
from app.services import subscription_service
from app.services.custom_plan_service import CustomPlanService
from app.services.notification_service import NotificationService
from app.services.subscription_service import next_period_start, renewal_lined_up
from app.utils.timezone import from_stored, now_ist

from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    gateway,
    mgr,
    pass_of,
    rig,
    vehicle_id_of,
)
from tests.test_reminder_loop import _stub_sweeps

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    holder = "holder-small"
    assert await main._claim_lease(db, holder)
    yield holder
    await db.locks.delete_many({"_id": main._LEASE_ID})


def _only(customer_id: str, monkeypatch) -> None:
    """The real pass finders, limited to this test's customer."""
    real_soon = subscription_service.find_subscriptions_expiring_soon
    real_ended = subscription_service.find_subscriptions_ended

    async def soon(db_, days=2, limit=200):
        return [s for s in await real_soon(db_, days=days, limit=5000) if s["customer_id"] == customer_id]

    async def ended(db_, limit=100, exclude_ids=()):
        others = await db_.user_subscriptions.distinct("_id", {"status": "active", "customer_id": {"$ne": customer_id}})
        return await real_ended(db_, limit=limit, exclude_ids=[*exclude_ids, *others])

    monkeypatch.setattr(subscription_service, "find_subscriptions_expiring_soon", soon)
    monkeypatch.setattr(subscription_service, "find_subscriptions_ended", ended)


async def test_renewed_pass_hears_it_continues_others_still_get_the_renew_note(db, rig, gateway, lease, monkeypatch):
    old = await activated_cash(db, rig)
    car_b = await vehicle_id_of(db, rig, rig["plate_b"])
    # Both old passes end tomorrow; only car A is renewed (paid).
    for vid in (rig["car_a"], car_b):
        await db.user_subscriptions.update_one({"custom_plan_id": old["id"], "vehicle_id": vid}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(cars=[
        {"vehicle_id": rig["car_a"], "items": [{"service_id": rig["svc"]["star-wash"], "count": 2}]},
    ]), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    old_a, old_b = await pass_of(db, old["id"], rig["car_a"]), await pass_of(db, old["id"], car_b)
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    assert old_a["renewed_by_custom_plan_id"] == renewal["id"] and not old_b.get("renewed_by_custom_plan_id")
    start_day = from_stored(new_a["start_date"])
    start_label = f"{start_day.day} {start_day.strftime('%b %Y')}"  # no leading zero
    continues = f"Your plan continues on {start_label} — next period already paid."

    told: list[dict] = []

    async def notify(self, user_id, title, message, *args, **kwargs):
        told.append({"user_id": user_id, "title": title, "message": message, "ref": args[1] if len(args) > 1 else kwargs.get("reference_id"),
                     "wa_event": kwargs.get("wa_event"), "wa_marketing": kwargs.get("wa_marketing", False)})

    monkeypatch.setattr(NotificationService, "notify", notify)
    _stub_sweeps(monkeypatch, [], keep=("find_subscriptions_expiring_soon", "find_subscriptions_ended"))
    _only(rig["customer"], monkeypatch)

    # 1) Ending soon.
    await main._sweep_once(db, lease)
    by_ref = {t["ref"]: t for t in told}
    a, b = by_ref[str(old_a["_id"])], by_ref[str(old_b["_id"])]
    assert continues in a["message"] and "renew" not in a["message"].lower()
    assert a["wa_event"] is None and not a["wa_marketing"]  # never the "renew to keep going" template
    assert "renew to keep going" in b["message"] and b["wa_event"] == "subscription_expiring"
    assert str(new_a["_id"]) not in by_ref  # the scheduled pass itself isn't "ending"

    # 2) The old period ends.
    told.clear()
    past = now_ist() - timedelta(hours=2)
    for p in (old_a, old_b):
        await db.user_subscriptions.update_one({"_id": p["_id"]}, {"$set": {"end_date": past}})
    await main._sweep_once(db, lease)
    by_ref = {t["ref"]: t for t in told}
    a, b = by_ref[str(old_a["_id"])], by_ref[str(old_b["_id"])]
    assert a["message"] == continues and "ended" not in a["title"] and a["wa_event"] is None
    assert b["message"] == "Renew any time to keep your car shining." and b["wa_event"] == "subscription_expired"
    statuses = {str(s["_id"]): s["status"] for s in await db.user_subscriptions.find({"_id": {"$in": [old_a["_id"], old_b["_id"]]}}).to_list(5)}
    assert set(statuses.values()) == {"expired"}


async def test_renewal_lined_up_follows_the_successor(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    await service.mark_cash_paid(renewal["id"], expected_revision=1, note=None, **mgr(rig))
    old_a = await pass_of(db, old["id"], rig["car_a"])
    new_a = await pass_of(db, renewal["id"], rig["car_a"])
    start = await renewal_lined_up(db, old_a)
    assert start is not None and from_stored(start) == from_stored(new_a["start_date"])
    # No renewal: nothing lined up.
    assert await renewal_lined_up(db, {"_id": ObjectId()}) is None
    # Marked, pass not created yet (activation in flight): the day after the
    # Last Booking Day.
    in_flight = {**old_a, "renewed_by_subscription_id": None, "renewed_by_custom_plan_id": str(ObjectId())}
    assert await renewal_lined_up(db, in_flight) == next_period_start(old_a)
    # The renewal of car A refunded: the old pass hears the renew note again.
    await service.refund_car(renewal["id"], rig["car_a"], amount=None, reason="Not renewing this car", **mgr(rig))
    old_a = await db.user_subscriptions.find_one({"_id": old_a["_id"]})
    assert await renewal_lined_up(db, old_a) is None
    # A successor cancelled behind a still-marked pass doesn't count either.
    old_b = await pass_of(db, old["id"], await vehicle_id_of(db, rig, rig["plate_b"]))
    new_b = await pass_of(db, renewal["id"], await vehicle_id_of(db, rig, rig["plate_b"]))
    await db.user_subscriptions.update_one({"_id": new_b["_id"]}, {"$set": {"status": "cancelled"}})
    assert await renewal_lined_up(db, old_b) is None
