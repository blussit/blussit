"""
Auto-pay passes through a cycle (2026-09-28):

  a. using the last wash keeps an auto-pay pass ACTIVE at 0 (no second pass
     can be bought while the mandate still bills); booking with it is
     refused cleanly; auto-pay going off at 0 expires it;
  b. no "ends soon — renew" notice for a pass that will renew, and a renewal
     resets the cycle's reminder stamps;
  c. the ended-pass sweep gives auto-pay passes grace (longer while Razorpay
     retries a failed charge);
  d. a mandate Razorpay stops (halted/cancelled — not the customer's own
     turn-off) is announced once, to the customer and the center's managers;
     "pending" (retrying) is recorded silently; the stop/retry webhooks move
     the mandate to the front of the next poll.

Razorpay is stubbed throughout — no external call is ever made.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.subscription_schema import SubscribeRequest
from app.services import payment_service
from app.services.payment_service import PaymentService
from app.services.subscription_service import (
    UserSubscriptionService,
    find_subscriptions_ended,
    find_subscriptions_expiring_soon,
)
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_customer,
    make_manager,
    make_service_center,
    make_subscription_plan,
)

pytestmark = pytest.mark.asyncio


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def rig(db, cleanup):
    customer_id = await make_customer(db, name="Arjun Autopay")
    plan_id = await make_subscription_plan(db, vehicle_types=[], total_service_count=1)
    for coll, flt in (
        ("users", {"_id": ObjectId(customer_id)}), ("user_subscriptions", {"customer_id": customer_id}),
        ("payment_orders", {"customer_id": customer_id}), ("notifications", {"user_id": customer_id}),
        ("bookings", {"customer_id": customer_id}), ("subscription_plans", {"_id": ObjectId(plan_id)}),
    ):
        cleanup.append((coll, flt))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    return {
        "customer_id": customer_id, "plan_id": plan_id, "phone": phone,
        "hatchback": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db),
    }


async def _buy(db, rig, mandate: str | None) -> dict:
    """A one-wash hatchback pass — on auto-pay when `mandate` is given."""
    return await UserSubscriptionService(db).subscribe(
        rig["customer_id"],
        SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"], auto_renew=bool(mandate)),
        razorpay_subscription_id=mandate,
    )


async def _use_one_wash(db, rig, sub_id: str) -> None:
    svc = UserSubscriptionService(db)
    star = await db.services.find_one({"_id": ObjectId(rig["star"])})
    consumption = await svc.plan_consumption(sub_id, None, [star], rig["customer_id"], vehicle_type=rig["hatchback"])
    await svc.commit_consumption(sub_id, consumption)


# --------------------------------------------------- a. 0 washes on auto-pay


async def test_autopay_pass_stays_active_at_zero_and_blocks_a_duplicate(db, rig):
    sub = await _buy(db, rig, mandate=f"sub_life_{ObjectId()}")
    await _use_one_wash(db, rig, sub["id"])

    stored = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert stored["remaining_service_count"] == 0
    assert stored["status"] == "active", "an auto-pay pass waits at 0 for its refill"
    assert stored.get("last_used_at") is not None

    svc = UserSubscriptionService(db)
    purchase = SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    with pytest.raises(BadRequestException, match="auto-pay. It refills on"):
        await svc.validate_purchase(rig["customer_id"], purchase)

    # Booking with it again is a clean refusal, not a negative count.
    star = await db.services.find_one({"_id": ObjectId(rig["star"])})
    with pytest.raises(BadRequestException, match="All washes on this pass are used"):
        await svc.plan_consumption(sub["id"], None, [star], rig["customer_id"], vehicle_type=rig["hatchback"])
    # ...and the quick-booking auto-apply never picks it.
    from app.services.booking_service import BookingService

    assert await BookingService(db)._usable_passes(rig["customer_id"]) == []


async def test_one_time_pass_still_expires_at_zero(db, rig):
    sub = await _buy(db, rig, mandate=None)
    await _use_one_wash(db, rig, sub["id"])
    stored = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert stored["remaining_service_count"] == 0 and stored["status"] == "expired"


async def test_auto_pay_going_off_at_zero_expires_the_pass(db, rig):
    svc = UserSubscriptionService(db)
    spent = await _buy(db, rig, mandate=f"sub_life_{ObjectId()}")
    await _use_one_wash(db, rig, spent["id"])
    await svc.mark_auto_renew_off(spent["id"])
    stored = await db.user_subscriptions.find_one({"_id": ObjectId(spent["id"])})
    assert stored["auto_renew"] is False and stored["status"] == "expired", "nothing left, no refill coming"
    # ...so buying it again is open straight away.
    await svc.validate_purchase(rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"]))

    # With washes left it just stops renewing.
    live = await _buy(db, rig, mandate=f"sub_life_{ObjectId()}")
    await svc.mark_auto_renew_off(live["id"])
    stored = await db.user_subscriptions.find_one({"_id": ObjectId(live["id"])})
    assert stored["auto_renew"] is False and stored["status"] == "active" and stored["remaining_service_count"] == 1


# ------------------------------------------- b. notices + renewal resets


async def test_renewal_refills_and_resets_the_cycles_reminders(db, rig):
    sub = await _buy(db, rig, mandate=f"sub_life_{ObjectId()}")
    await _use_one_wash(db, rig, sub["id"])
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {
        "expiry_reminder_sent": True, "wash_reminder_sent_at": _utc_now(), "used_up_notice_sent_at": _utc_now(),
        "autopay_state": "pending",
    }})
    renewed = await UserSubscriptionService(db).apply_renewal_cycle(sub["id"])
    assert renewed["status"] == "active" and renewed["remaining_service_count"] == 1
    stored = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert stored["expiry_reminder_sent"] is False
    assert stored["wash_reminder_sent_at"] is None and stored["used_up_notice_sent_at"] is None
    assert stored["autopay_state"] is None


async def test_ends_soon_notice_skips_passes_that_will_renew(db, rig):
    soon = _utc_now() + timedelta(days=1)
    base = {"customer_id": rig["customer_id"], "plan_id": rig["plan_id"], "status": "active", "is_deleted": False,
            "remaining_service_count": 2, "start_date": _utc_now() - timedelta(days=29), "end_date": soon}
    renewing = (await db.user_subscriptions.insert_one({**base, "auto_renew": True, "razorpay_subscription_id": "sub_x"})).inserted_id
    one_time = (await db.user_subscriptions.insert_one({**base, "auto_renew": False})).inserted_id
    flag_only = (await db.user_subscriptions.insert_one({**base, "auto_renew": True, "razorpay_subscription_id": None})).inserted_id
    found = {s["_id"] for s in await find_subscriptions_expiring_soon(db, days=2, limit=500)}
    assert one_time in found and flag_only in found, "no mandate behind it = it really is ending"
    assert renewing not in found


# ------------------------------------------------- c. ended-pass grace


async def test_ended_sweep_gives_auto_pay_passes_grace(db, rig):
    now = _utc_now()
    base = {"customer_id": rig["customer_id"], "plan_id": rig["plan_id"], "status": "active", "is_deleted": False,
            "remaining_service_count": 0, "start_date": now - timedelta(days=40)}
    autopay = {**base, "auto_renew": True, "razorpay_subscription_id": "sub_grace"}

    async def insert(**fields):
        return (await db.user_subscriptions.insert_one(fields)).inserted_id

    one_time_just_ended = await insert(**base, end_date=now - timedelta(hours=1))
    autopay_charge_late = await insert(**autopay, end_date=now - timedelta(days=1))
    autopay_never_charged = await insert(**autopay, end_date=now - timedelta(days=3))
    retrying = await insert(**autopay, autopay_state="pending", end_date=now - timedelta(days=3))
    retrying_too_long = await insert(**autopay, autopay_state="pending", end_date=now - timedelta(days=8))

    ours = {one_time_just_ended, autopay_charge_late, autopay_never_charged, retrying, retrying_too_long}
    others = await db.user_subscriptions.distinct("_id", {"status": "active", "_id": {"$nin": list(ours)}})
    ended = {s["_id"] for s in await find_subscriptions_ended(db, limit=100, exclude_ids=others)}
    assert ended == {one_time_just_ended, autopay_never_charged, retrying_too_long}


# ------------------------------------ d. Razorpay stops or retries a mandate


class _Mandates:
    """Stub Razorpay subscriptions API: answers for OUR mandate only."""

    def __init__(self, mandate_id: str):
        self.mandate_id = mandate_id
        self.remote = {"status": "active", "paid_count": 1, "current_end": None}
        self.fetched: list[str] = []

    def fetch(self, subscription_id):
        self.fetched.append(subscription_id)
        if subscription_id != self.mandate_id:
            raise RuntimeError("not this test's mandate")
        return {"id": subscription_id, **self.remote}

    def cancel(self, subscription_id, data=None):
        return {"id": subscription_id, "status": "cancelled"}


@pytest.fixture
async def mandate(db, rig, cleanup, monkeypatch):
    """A center-sold auto-pay pass with a live mandate, and the stub gateway."""
    center_id = await make_service_center(db, pincode="452068")
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("notifications", {"user_id": manager_id}))

    mandate_id = f"sub_stop_{ObjectId()}"
    sub = await _buy(db, rig, mandate=mandate_id)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {"service_center_id": center_id}})
    await db.payment_orders.insert_one({
        "kind": "autopay", "status": "paid", "purpose": "subscription", "auto_pay_active": True,
        "razorpay_subscription_id": mandate_id, "customer_id": rig["customer_id"], "plan_id": rig["plan_id"],
        "subscription_id": sub["id"], "cycles_applied": 1, "amount_paise": 49900,
        # Oldest never-checked mandate = first in the sweep's queue.
        "created_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
    })
    stub = _Mandates(mandate_id)
    client = type("Client", (), {"subscription": stub})()
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)
    return {"id": mandate_id, "sub_id": sub["id"], "stub": stub, "manager_id": manager_id}


async def _due_now(db, mandate_id: str) -> None:
    await db.payment_orders.update_one({"razorpay_subscription_id": mandate_id}, {"$unset": {"next_check_at": ""}})


@pytest.mark.parametrize("remote_status", ["halted", "cancelled"])
async def test_a_mandate_razorpay_stops_is_announced_once(db, rig, mandate, remote_status):
    mandate["stub"].remote = {"status": remote_status, "paid_count": 1, "current_end": None}
    await PaymentService(db).sync_autopay_renewals()

    order = await db.payment_orders.find_one({"razorpay_subscription_id": mandate["id"]})
    assert order["auto_pay_active"] is False and order["mandate_status"] == remote_status
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(mandate["sub_id"])})
    assert sub["auto_renew"] is False

    plan = await db.subscription_plans.find_one({"_id": ObjectId(rig["plan_id"])})
    told = await db.notifications.find({"user_id": rig["customer_id"], "title": "Auto-pay stopped"}).to_list(None)
    assert [n["message"] for n in told] == [f"Auto-pay for your {plan['name']} pass stopped — renew from your dashboard."]
    # On WhatsApp too — the normal (utility) path.
    assert await db.whatsapp_outbox.count_documents({"phone": rig["phone"]}) == 1
    # The center's manager hears it in-app (never on WhatsApp).
    staff = await db.notifications.find({"user_id": mandate["manager_id"], "title": "Auto-pay stopped"}).to_list(None)
    assert len(staff) == 1 and "Arjun Autopay" in staff[0]["message"]
    manager = await db.users.find_one({"_id": ObjectId(mandate["manager_id"])})
    assert await db.whatsapp_outbox.count_documents({"phone": manager["phone"]}) == 0

    # Never polled (or announced) again.
    await _due_now(db, mandate["id"])
    fetched = len(mandate["stub"].fetched)
    await PaymentService(db).sync_autopay_renewals()
    assert mandate["id"] not in mandate["stub"].fetched[fetched:]
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Auto-pay stopped"}) == 1


async def test_the_customers_own_turn_off_is_not_announced(db, rig, mandate):
    await UserSubscriptionService(db).set_auto_pay(rig["customer_id"], mandate["sub_id"], False)
    mandate["stub"].remote = {"status": "cancelled", "paid_count": 1, "current_end": None}
    await _due_now(db, mandate["id"])
    await PaymentService(db).sync_autopay_renewals()
    # It IS still checked until Razorpay confirms it ended (a gateway cancel
    # can fail and the mandate keep charging) — but the customer's own
    # turn-off is never announced back to them, and once Razorpay says it's
    # over, it's never polled again.
    assert await db.notifications.count_documents({"title": "Auto-pay stopped", "user_id": {"$in": [rig["customer_id"], mandate["manager_id"]]}}) == 0
    assert (await db.payment_orders.find_one({"razorpay_subscription_id": mandate["id"]}))["mandate_final"] is True
    mandate["stub"].fetched.clear()
    await _due_now(db, mandate["id"])
    await PaymentService(db).sync_autopay_renewals()
    assert mandate["id"] not in mandate["stub"].fetched


async def test_a_retrying_charge_is_recorded_quietly_and_cleared_when_it_settles(db, rig, mandate):
    mandate["stub"].remote = {"status": "pending", "paid_count": 1, "current_end": None}
    await PaymentService(db).sync_autopay_renewals()
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(mandate["sub_id"])})
    assert sub["autopay_state"] == "pending" and sub["auto_renew"] is True and sub["status"] == "active"
    assert (await db.payment_orders.find_one({"razorpay_subscription_id": mandate["id"]}))["auto_pay_active"] is True
    assert await db.notifications.count_documents({"user_id": rig["customer_id"], "title": "Auto-pay stopped"}) == 0

    # The retry went through: Razorpay charged cycle 2 and is active again.
    mandate["stub"].remote = {"status": "active", "paid_count": 2, "current_end": int((now_ist() + timedelta(days=60)).timestamp())}
    await _due_now(db, mandate["id"])
    assert await PaymentService(db).sync_autopay_renewals() == 1
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(mandate["sub_id"])})
    assert sub["autopay_state"] is None and sub["renewal_count"] == 1


@pytest.mark.parametrize("event", ["subscription.halted", "subscription.pending", "subscription.cancelled"])
async def test_stop_and_retry_webhooks_move_the_mandate_to_the_front_of_the_poll(db, rig, mandate, event):
    await db.payment_orders.update_one(
        {"razorpay_subscription_id": mandate["id"]},
        {"$set": {"next_check_at": now_ist() + timedelta(hours=12), "last_checked_at": now_ist()}},
    )
    outcome = await PaymentService(db)._dispatch_webhook(event, {"subscription": {"entity": {"id": mandate["id"]}}})
    assert outcome == "mandate_nudged"
    order = await db.payment_orders.find_one({"razorpay_subscription_id": mandate["id"]})
    assert "last_checked_at" not in order
    assert order["next_check_at"].replace(tzinfo=timezone.utc) <= _utc_now()


def test_pass_in_autopay_grace_reads_renewing_not_ended():
    from datetime import datetime, timedelta, timezone

    from app.services.subscription_service import _with_effective_status

    base = {"_id": "x", "status": "active", "auto_renew": True, "razorpay_subscription_id": "sub_1"}
    just_ended = {**base, "end_date": (datetime.now(timezone.utc) - timedelta(hours=3)).replace(tzinfo=None)}
    doc = _with_effective_status(just_ended)
    assert doc["effective_status"] == "expired" and doc.get("renewal_pending") is True

    long_gone = {**base, "end_date": (datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None)}
    assert not _with_effective_status(long_gone).get("renewal_pending")

    one_time = {**just_ended, "auto_renew": False}
    assert not _with_effective_status(one_time).get("renewal_pending")


async def test_no_duplicate_purchase_while_auto_pay_is_renewing(db, rig):
    sub = await _buy(db, rig, mandate=f"sub_grace_{ObjectId()}")
    # Ended an hour ago; Razorpay's next charge hasn't landed yet.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(sub["id"])}, {"$set": {"end_date": (_utc_now() - timedelta(hours=1)).replace(tzinfo=None)}}
    )
    purchase = SubscribeRequest(plan_id=rig["plan_id"], vehicle_type=rig["hatchback"], service_id=rig["star"])
    with pytest.raises(BadRequestException, match="renewing now"):
        await UserSubscriptionService(db).validate_purchase(rig["customer_id"], purchase)

    # Once the grace window is over (mandate never paid), buying again is fine.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(sub["id"])}, {"$set": {"end_date": (_utc_now() - timedelta(days=3)).replace(tzinfo=None)}}
    )
    await UserSubscriptionService(db).validate_purchase(rig["customer_id"], purchase)
