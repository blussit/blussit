"""Generalised pass extension (founder rule 2026-10-07): ANY pass — custom,
monthly or society — can be extended by a manager of its own center or an
admin, at most 10 days per 30-day period, only in its last 3 days or after
it ended, while washes remain; customers never extend. Reviving an ENDED
pass re-checks that its car hasn't picked up another live pass meanwhile
(the gap the society-only version had). Local Mongo only."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException, ForbiddenException, NotFoundException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import (
    PASS_EXTENSION_MAX_DAYS,
    PassClaimConflict,
    UserSubscriptionService,
    find_subscriptions_ended,
    mark_subscription_expired,
)
from app.utils.timezone import from_stored, now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_customer,
    make_manager,
    make_service_center,
    make_subscription_plan,
    make_vehicle,
)
from tests.society_factories import auth, client, make_admin

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
async def _tidy(db):
    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    first = ObjectId.from_datetime(since)
    yield
    for name in ("user_subscriptions", "payment_orders", "custom_plans", "bookings", "notifications", "audit_logs"):
        await db[name].delete_many({"_id": {"$gte": first}})
    await db.pass_claims.delete_many({"created_at": {"$gte": since}})


@pytest.fixture
async def rig(db, cleanup):
    center = await make_service_center(db)
    other = await make_service_center(db)
    manager = await make_manager(db, center)
    other_manager = await make_manager(db, other)
    admin = await make_admin(db, cleanup)
    customer = await make_customer(db)
    hatch = await get_hatchback_type_id(db)
    car = await make_vehicle(db, customer, hatch, registration_number=f"MP09EX{next(_seq):04d}")
    star = await get_star_wash_service_id(db)
    for uid in (manager, other_manager, customer):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("vehicles", {"owner_id": customer}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    await db.bookings.insert_one({"customer_id": customer, "service_center_id": center, "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKEX{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    return {"center": center, "other": other, "manager": manager, "other_manager": other_manager, "admin": admin,
            "customer": customer, "hatch": hatch, "car": car, "star": star}


async def _custom_pass(db, rig, *, ends_in: timedelta = timedelta(days=1)) -> str:
    service = CustomPlanService(db)
    cart = await service.create(
        CustomPlanCreateRequest(customer_id=rig["customer"], cars=[{"vehicle_id": rig["car"], "items": [{"service_id": rig["star"], "count": 3}]}]),
        actor_id=rig["manager"], actor_role="manager", actor_center_id=rig["center"],
    )
    result = await service.mark_cash_paid(cart["id"], expected_revision=1, note=None, actor_id=rig["manager"], actor_role="manager", actor_center_id=rig["center"])
    sub_id = result["subscription_ids"][0]
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() + ends_in}})
    return sub_id


def _mgr(rig) -> dict:
    return {"actor_id": rig["manager"], "actor_role": "manager", "actor_center_id": rig["center"]}


def _adm(rig) -> dict:
    return {"actor_id": rig["admin"], "actor_role": "admin", "actor_center_id": None}


async def test_custom_pass_extension_cap_history_and_views(db, rig):
    sub_id = await _custom_pass(db, rig)
    end = (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["end_date"]
    subs = UserSubscriptionService(db)
    view = await subs.extend_pass(sub_id, 4, note="Rain week", **_mgr(rig))
    assert view["extension_days"] == 4 and view["extension_days_left"] == 6 and view["custom_plan_id"]
    with pytest.raises(BadRequestException) as exc:
        await subs.extend_pass(sub_id, 7, note=None, **_adm(rig))
    assert str(PASS_EXTENSION_MAX_DAYS) in exc.value.message
    view = await subs.extend_pass(sub_id, 6, note=None, **_adm(rig))
    assert view["extension_days"] == 10 and view["extension_days_left"] == 0 and not view["can_extend"]
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert [h["days"] for h in raw["extensions"]] == [4, 6] and [h["role"] for h in raw["extensions"]] == ["manager", "admin"]
    assert raw["extensions"][0]["by"] == rig["manager"] and raw["extensions"][0]["note"] == "Rain week"
    assert from_stored(raw["extended_until"]).date() == from_stored(end + timedelta(days=10)).date()
    # Customer sees until when (not who); staff rows carry the history.
    mine = next(s for s in await subs.list_my_subscriptions(rig["customer"]) if s["id"] == sub_id)
    assert mine["extension_days"] == 10 and mine["extended_until"] and "extensions" not in mine and "can_extend" not in mine
    ov = await subs.center_overview(rig["center"], "manager", rig["center"])
    row = next(r for r in ov["rows"] if r["subscription_id"] == sub_id)
    assert row["extension_days"] == 10 and len(row["extensions"]) == 2


async def test_window_washes_and_status_rules(db, rig):
    subs = UserSubscriptionService(db)
    fresh = await _custom_pass(db, rig, ends_in=timedelta(days=20))
    with pytest.raises(BadRequestException):  # not in its last 3 days yet
        await subs.extend_pass(fresh, 2, note=None, **_mgr(rig))
    await db.user_subscriptions.update_one({"_id": ObjectId(fresh)}, {"$set": {"end_date": now_ist() + timedelta(days=2, hours=23)}})
    assert (await subs.extend_pass(fresh, 2, note=None, **_mgr(rig)))["extension_days"] == 2
    await db.user_subscriptions.update_one({"_id": ObjectId(fresh)}, {"$set": {"remaining_service_count": 0, "remaining_by_service": {rig["star"]: 0}}})
    with pytest.raises(BadRequestException):  # no washes left
        await subs.extend_pass(fresh, 2, note=None, **_mgr(rig))
    await db.user_subscriptions.update_one({"_id": ObjectId(fresh)}, {"$set": {"remaining_service_count": 2, "status": "cancelled"}})
    with pytest.raises(BadRequestException):  # cancelled
        await subs.extend_pass(fresh, 2, note=None, **_mgr(rig))
    await db.user_subscriptions.update_one({"_id": ObjectId(fresh)}, {"$set": {"status": "expired", "end_date": now_ist() - timedelta(days=12)}})
    with pytest.raises(BadRequestException):  # ended so long ago even 10 days are over
        await subs.extend_pass(fresh, 2, note=None, **_mgr(rig))
    for bad in (0, 11):
        with pytest.raises(BadRequestException):
            await subs.extend_pass(fresh, bad, note=None, **_adm(rig))
    # An auto-pay pass renews itself — no extension.
    await db.user_subscriptions.update_one({"_id": ObjectId(fresh)}, {"$set": {
        "status": "active", "end_date": now_ist() + timedelta(days=1), "auto_renew": True, "razorpay_subscription_id": "sub_x", "extension_days": 0}})
    with pytest.raises(BadRequestException):
        await subs.extend_pass(fresh, 2, note=None, **_adm(rig))


async def test_who_may_extend_over_http(db, rig):
    sub_id = await _custom_pass(db, rig)
    url = f"/api/v1/subscriptions/{sub_id}/extend"
    async with client() as c:
        r = await c.post(url, json={"days": 2}, headers=auth(rig["customer"], "customer"))
        assert r.status_code == 403, r.text
        r = await c.post(url, json={"days": 2}, headers=auth(rig["other_manager"], "manager", rig["other"]))
        assert r.status_code == 403, r.text
        r = await c.post(url, json={"days": 11}, headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 422, r.text
        r = await c.post(f"/api/v1/subscriptions/{ObjectId()}/extend", json={"days": 2}, headers=auth(rig["admin"], "admin"))
        assert r.status_code == 404, r.text
        r = await c.post(url, json={"days": 3, "note": "Captain on leave"}, headers=auth(rig["manager"], "manager", rig["center"]))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["extension_days"] == 3
    audit = await db.audit_logs.find_one({"action": "EXTEND_PASS", "target_id": sub_id})
    assert audit and audit["details"]["days"] == 3 and audit["details"]["custom_plan_id"]


async def test_self_serve_monthly_pass_is_admin_only(db, rig):
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatch"]], included_service_ids=[rig["star"]], total_service_count=2)
    monthly = await UserSubscriptionService(db).assign(AssignSubscriptionRequest(customer_id=rig["customer"], plan_id=plan_id, vehicle_type=rig["hatch"], service_id=rig["star"]))
    await db.user_subscriptions.update_one({"_id": ObjectId(monthly["id"])}, {"$set": {"end_date": now_ist() + timedelta(hours=20)}})
    subs = UserSubscriptionService(db)
    with pytest.raises(ForbiddenException):  # no center: a manager can't own it
        await subs.extend_pass(monthly["id"], 2, note=None, **_mgr(rig))
    view = await subs.extend_pass(monthly["id"], 2, note=None, **_adm(rig))
    assert view["extension_days"] == 2 and view["plan_name"]
    with pytest.raises(ForbiddenException):
        await subs.extend_pass(monthly["id"], 2, note=None, actor_id=rig["customer"], actor_role="customer", actor_center_id=None)


async def test_bookable_inside_the_extension_only(db, rig):
    sub_id = await _custom_pass(db, rig, ends_in=timedelta(days=1, hours=2))
    subs = UserSubscriptionService(db)
    star = await db.services.find_one({"_id": ObjectId(rig["star"])})
    end = (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["end_date"]
    after_end = from_stored(end).date() + timedelta(days=1)
    with pytest.raises(BadRequestException):
        await subs.plan_consumption(sub_id, rig["car"], [star], rig["customer"], scheduled_date=after_end.isoformat())
    await subs.extend_pass(sub_id, 3, note=None, **_mgr(rig))
    plan = await subs.plan_consumption(sub_id, rig["car"], [star], rig["customer"], scheduled_date=after_end.isoformat())
    assert plan["covered_service_ids"] == [rig["star"]]
    with pytest.raises(BadRequestException):  # on/after extended_until
        await subs.plan_consumption(sub_id, rig["car"], [star], rig["customer"], scheduled_date=(from_stored(end).date() + timedelta(days=3)).isoformat())
    # Past the plan's end, inside the extension: usable, swept only after.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() - timedelta(hours=1)}})
    mine = next(s for s in await subs.list_my_subscriptions(rig["customer"]) if s["id"] == sub_id)
    assert mine["effective_status"] == "active" and mine["in_extension"]
    assert sub_id not in {str(s["_id"]) for s in await find_subscriptions_ended(db, limit=500)}
    assert (await subs.plan_consumption(sub_id, rig["car"], [star], rig["customer"]))["by_service"] == {rig["star"]: 1}


async def test_revive_rechecks_the_car_claim(db, rig):
    subs = UserSubscriptionService(db)
    old = await _custom_pass(db, rig, ends_in=-timedelta(hours=3))
    await mark_subscription_expired(db, old)
    # The car got a NEW pass after the old one ended …
    plan_id = await make_subscription_plan(db, vehicle_types=[rig["hatch"]], included_service_ids=[rig["star"]], total_service_count=2)
    newer = await subs.assign(AssignSubscriptionRequest(customer_id=rig["customer"], plan_id=plan_id, vehicle_id=rig["car"], service_id=rig["star"]),
                              actor_center_id=rig["center"])
    # … so the old one can't be brought back over it.
    with pytest.raises(BadRequestException):
        await subs.extend_pass(old, 3, note=None, **_mgr(rig))
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(old)})
    assert raw["status"] == "expired" and int(raw.get("extension_days") or 0) == 0
    # Once the newer one is gone, the revive goes through and re-takes the
    # car's claim: no third pass can be sold for that car meanwhile.
    await subs.repo.update_by_id(newer["id"], {"status": "cancelled"})
    await db.pass_claims.delete_many({"subscription_id": newer["id"]})
    view = await subs.extend_pass(old, 3, note=None, **_mgr(rig))
    assert view["extension_days"] == 3 and (await db.user_subscriptions.find_one({"_id": ObjectId(old)}))["status"] == "active"
    claim = await db.pass_claims.find_one({"_id": f"vehicle:{rig['customer']}:{rig['car']}"})
    assert claim and claim["subscription_id"] == old
    with pytest.raises((BadRequestException, PassClaimConflict)):
        await subs.assign(AssignSubscriptionRequest(customer_id=rig["customer"], plan_id=plan_id, vehicle_id=rig["car"], service_id=rig["star"]),
                          actor_center_id=rig["center"])


async def test_concurrent_extends_never_pass_the_cap(db, rig):
    sub_id = await _custom_pass(db, rig, ends_in=-timedelta(hours=2))
    await mark_subscription_expired(db, sub_id)

    async def go():
        try:
            await UserSubscriptionService(db).extend_pass(sub_id, 4, note=None, **_mgr(rig))
            return 4
        except AppException:
            return 0

    granted = await asyncio.gather(*[go() for _ in range(5)])
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sum(granted) == raw["extension_days"] == 8 and len(raw["extensions"]) == 2
    assert raw["status"] == "active"


async def test_unknown_or_foreign_pass_is_404_or_403(db, cleanup, rig):
    subs = UserSubscriptionService(db)
    with pytest.raises(NotFoundException):
        await subs.extend_pass(str(ObjectId()), 2, note=None, **_adm(rig))
    sub_id = await _custom_pass(db, rig)
    with pytest.raises(ForbiddenException):
        await subs.extend_pass(sub_id, 2, note=None, actor_id=rig["other_manager"], actor_role="manager", actor_center_id=rig["other"])
    # A manager with no center linked: fails closed.
    lone = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(lone)}))
    with pytest.raises(ForbiddenException):
        await subs.extend_pass(sub_id, 2, note=None, actor_id=lone, actor_role="manager", actor_center_id=None)
