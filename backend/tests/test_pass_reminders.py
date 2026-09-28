"""
Pass reminders (2026-09-28):

  - "3 washes left on your pass — book your next wash": a live pass with
    washes left and nothing booked on it, at most weekly, never within a few
    days of buying/booking, never while the "ends soon" notice covers it.
    WhatsApp reuses the approved blussit_subscription_expiring template as
    MARKETING (honours the opt-out, no utility fallback).
  - Customer reminders (wash nudge, expiring/ended pass notices, repeat
    nudge) only go out 10 AM–7 PM IST; outside it nothing is stamped.
  - "You've used all washes": once per (non-auto-pay) pass, after its last
    wash is actually done.

The loop's other sweeps are stubbed (tests.test_reminder_loop._stub_sweeps)
so a pass never touches Razorpay or other tests' rows.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app import main
from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
from app.schemas.subscription_schema import SubscribeRequest
from app.services import subscription_service
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.subscription_service import UserSubscriptionService, find_passes_due_wash_reminder
from app.utils.timezone import IST, from_stored, in_customer_message_hours, now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_customer,
    make_manager,
    make_service_center,
    make_subscription_plan,
)
from tests.test_reminder_loop import _stub_sweeps, lease  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def holder(db, cleanup):
    customer_id = await make_customer(db, name="Pooja Pass")
    plan_id = await make_subscription_plan(db, vehicle_types=[], total_service_count=4)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    return {"customer_id": customer_id, "plan_id": plan_id, "phone": phone}


async def _pass(db, holder, **fields) -> str:
    """A live pass bought 10 days ago, 3 of 4 washes left, 20 days to go."""
    now = _utc_now()
    doc = {
        "customer_id": holder["customer_id"], "plan_id": holder["plan_id"], "status": "active", "is_deleted": False,
        "total_service_count": 4, "remaining_service_count": 3,
        "start_date": now - timedelta(days=10), "end_date": now + timedelta(days=20),
        "auto_renew": False, "razorpay_subscription_id": None,
        "created_at": now - timedelta(days=10), "updated_at": now,
    }
    doc.update(fields)
    return str((await db.user_subscriptions.insert_one(doc)).inserted_id)


def _record_notify(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def notify(self, user_id, title, message, notification_type=None, reference_id=None, **kwargs):
        sent.append({"user_id": user_id, "title": title, "message": message, "reference_id": reference_id, **kwargs})

    monkeypatch.setattr(NotificationService, "notify", notify)
    return sent


# ------------------------------------------------------------ quiet hours


async def test_customer_message_hours_are_10_am_to_7_pm_ist():
    day = datetime(2026, 9, 28, tzinfo=IST)
    assert not in_customer_message_hours(day.replace(hour=9, minute=59))
    assert in_customer_message_hours(day.replace(hour=10))
    assert in_customer_message_hours(day.replace(hour=18, minute=59))
    assert not in_customer_message_hours(day.replace(hour=19))
    assert not in_customer_message_hours(day.replace(hour=2))
    # An instant given in UTC is judged by its IST wall clock (05:00 UTC = 10:30 IST).
    assert in_customer_message_hours(datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc))


async def test_customer_notices_wait_outside_customer_hours(db, lease, holder, monkeypatch):  # noqa: F811
    ended = await _pass(db, holder, end_date=_utc_now() - timedelta(hours=3))
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    monkeypatch.setattr(main, "_customer_hours_open", lambda: False)
    sent = _record_notify(monkeypatch)

    await main._sweep_once(db, lease)

    for skipped in ("find_passes_due_wash_reminder", "find_subscriptions_expiring_soon", "find_subscriptions_ended",
                    "find_customers_due_repeat_reminder"):
        assert skipped not in calls, f"{skipped} ran outside customer hours"
    assert "find_bookings_needing_reminder" in calls, "operational sweeps don't wait"
    assert sent == []
    # Nothing was stamped or claimed — the first daytime pass does it all.
    lock = await db.locks.find_one({"_id": main._LEASE_ID})
    assert not lock.get("repeat_sweep_at") and not lock.get("wash_reminder_sweep_at")
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(ended)}))["status"] == "active"


# ---------------------------------------------------------- wash reminder


async def test_wash_reminder_finder_skips_passes_that_should_not_be_nagged(db, holder):
    now = _utc_now()
    due = await _pass(db, holder)
    reminded_long_ago = await _pass(db, holder, wash_reminder_sent_at=now - timedelta(days=8))
    skipped = {
        "just bought": await _pass(db, holder, start_date=now - timedelta(days=1)),
        "just booked": await _pass(db, holder, last_used_at=now - timedelta(days=1)),
        "just renewed": await _pass(db, holder, last_renewed_at=now - timedelta(hours=5)),
        "reminded this week": await _pass(db, holder, wash_reminder_sent_at=now - timedelta(days=2)),
        "no washes left": await _pass(db, holder, remaining_service_count=0),
        "ends soon (the expiring notice covers it)": await _pass(db, holder, end_date=now + timedelta(days=1)),
        "already ended": await _pass(db, holder, end_date=now - timedelta(hours=1)),
        "expired": await _pass(db, holder, status="expired"),
        "cancelled": await _pass(db, holder, status="cancelled"),
    }
    booked = await _pass(db, holder)
    await db.bookings.insert_one({
        "customer_id": holder["customer_id"], "subscription_id": booked, "status": "assigned", "is_deleted": False,
        "booking_number": f"WR-{booked}",
    })
    skipped["a wash already booked on it"] = booked
    # A DONE booking on a pass doesn't count as "already booked".
    done_before = await _pass(db, holder)
    await db.bookings.insert_one({
        "customer_id": holder["customer_id"], "subscription_id": done_before, "status": "completed", "is_deleted": False,
        "booking_number": f"WR-{done_before}",
    })

    found = {str(s["_id"]) for s in await find_passes_due_wash_reminder(db, limit=500)}
    assert {due, reminded_long_ago, done_before} <= found
    for why, sub_id in skipped.items():
        assert sub_id not in found, f"reminded a pass that should be skipped: {why}"


async def test_wash_reminder_goes_out_as_marketing_weekly_and_at_most_hourly(db, lease, holder, monkeypatch):  # noqa: F811
    sub_id = await _pass(db, holder)
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, keep=("find_passes_due_wash_reminder",))
    real = subscription_service.find_passes_due_wash_reminder

    async def ours(db_, *args, **kwargs):
        return [s for s in await real(db_, *args, **kwargs) if s["customer_id"] == holder["customer_id"]]

    monkeypatch.setattr(subscription_service, "find_passes_due_wash_reminder", ours)
    sent = _record_notify(monkeypatch)

    await main._sweep_once(db, lease)
    assert len(sent) == 1
    note = sent[0]
    plan = await db.subscription_plans.find_one({"_id": ObjectId(holder["plan_id"])})
    stored = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert note["user_id"] == holder["customer_id"] and note["reference_id"] == sub_id
    assert note["message"] == f"3 washes left on your {plan['name']} pass — book your next wash."
    assert note["wa_event"] == "pass_wash_reminder" and note["wa_marketing"] is True
    assert note["wa_params"] == ["Pooja", plan["name"], from_stored(stored["end_date"]).strftime("%d %b"), "3"]
    assert stored.get("wash_reminder_sent_at") is not None

    # Same hour: the sweep isn't even re-run.
    await main._sweep_once(db, lease)
    assert len(sent) == 1
    # An hour later it runs again, but the pass is inside its week.
    await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"wash_reminder_sweep_at": _utc_now() - timedelta(hours=2)}})
    await main._sweep_once(db, lease)
    assert len(sent) == 1
    # A week on, it's due again.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"wash_reminder_sent_at": _utc_now() - timedelta(days=7, minutes=1)}})
    await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"wash_reminder_sweep_at": _utc_now() - timedelta(hours=2)}})
    await main._sweep_once(db, lease)
    assert len(sent) == 2


async def test_wash_reminder_whatsapp_uses_the_expiring_template_and_honours_opt_out(db, holder, cleanup):
    await db.whatsapp_templates.update_one(
        {"name": "blussit_subscription_expiring"},
        {"$set": {"name": "blussit_subscription_expiring", "status": "APPROVED", "category": "MARKETING", "param_count": 4, "disabled": False}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": "blussit_subscription_expiring"}))
    notifications = NotificationService(db)

    async def send():
        await notifications.notify(
            holder["customer_id"], "Book your next wash", "3 washes left on your Test pass — book your next wash.",
            reference_id="x", wa_event="pass_wash_reminder", wa_params=["Pooja", "Test", "12 Oct", "3"], wa_marketing=True,
        )

    await send()
    rows = await db.whatsapp_outbox.find({"phone": holder["phone"]}).to_list(None)
    assert [r.get("template_name") for r in rows] == ["blussit_subscription_expiring"]
    assert "Pooja, Test, 12 Oct, 3" in rows[0]["message"]

    await db.users.update_one({"_id": ObjectId(holder["customer_id"])}, {"$set": {"marketing_opt_out": True}})
    await send()
    assert await db.whatsapp_outbox.count_documents({"phone": holder["phone"]}) == 1, "opted out: in-app only"
    assert await db.notifications.count_documents({"user_id": holder["customer_id"], "title": "Book your next wash"}) == 2

    # Template not approved: marketing never falls back to the utility template.
    await db.users.update_one({"_id": ObjectId(holder["customer_id"])}, {"$set": {"marketing_opt_out": False}})
    await db.whatsapp_templates.update_one({"name": "blussit_subscription_expiring"}, {"$set": {"status": "PENDING"}})
    await send()
    assert await db.whatsapp_outbox.count_documents({"phone": holder["phone"]}) == 1


# ------------------------------------------------------- used all washes


async def test_used_up_note_goes_once_after_the_last_wash_is_done(db, holder):
    bs = BookingService(db)
    sub_id = await _pass(db, holder, status="expired", remaining_service_count=0)
    booking = await db.bookings.insert_one({
        "customer_id": holder["customer_id"], "subscription_id": sub_id, "status": "assigned", "is_deleted": False,
        "booking_number": f"UU-{sub_id}",
    })

    async def notes() -> int:
        return await db.notifications.count_documents({"user_id": holder["customer_id"], "title": "All washes used"})

    # The last wash is booked but not done yet — not "used" yet.
    await bs._after_visit_completed(holder["customer_id"], [{"subscription_id": sub_id}], now_ist())
    assert await notes() == 0

    await db.bookings.update_one({"_id": booking.inserted_id}, {"$set": {"status": "completed"}})
    await bs._after_visit_completed(holder["customer_id"], [{"subscription_id": sub_id}], now_ist())
    assert await notes() == 1
    note = await db.notifications.find_one({"user_id": holder["customer_id"], "title": "All washes used"})
    plan = await db.subscription_plans.find_one({"_id": ObjectId(holder["plan_id"])})
    assert note["message"] == f"You've used all washes on your {plan['name']} pass. Buy it again from your dashboard."
    assert note["reference_id"] == sub_id

    # Once only.
    await bs._after_visit_completed(holder["customer_id"], [{"subscription_id": sub_id}], now_ist())
    assert await notes() == 1

    # An auto-pay pass at 0 is still active (waiting for its refill) — no note.
    autopay = await _pass(db, holder, remaining_service_count=0, auto_renew=True, razorpay_subscription_id="sub_uu_1")
    await bs._after_visit_completed(holder["customer_id"], [{"subscription_id": autopay}], now_ist())
    assert await notes() == 1


async def test_last_pass_wash_logged_by_a_manager_sends_the_note_and_stamps_last_completed(db, holder, cleanup):
    hatchback, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    center_id = await make_service_center(db, pincode="452067")
    manager_id = await make_manager(db, center_id)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("addresses", {"owner_id": holder["customer_id"]}))
    one_wash = await make_subscription_plan(db, vehicle_types=[], total_service_count=1)
    cleanup.append(("subscription_plans", {"_id": ObjectId(one_wash)}))
    sub = await UserSubscriptionService(db).subscribe(
        holder["customer_id"], SubscribeRequest(plan_id=one_wash, vehicle_type=hatchback, service_id=star)
    )
    # Bought two days ago, so yesterday's job can use it.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {"start_date": _utc_now() - timedelta(days=2)}})

    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    result = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Pooja Pass", customer_phone=holder["phone"],
            lines=[QuickBookingLine(vehicle_type=hatchback, quantity=1, service_ids=[star])],
            scheduled_date=yesterday, service_time="10:30", address_line="Pass Lane 1, Indore", send_whatsapp=True,
        ),
        manager_id=manager_id, manager_center_id=center_id,
    )
    assert result["bookings"][0]["subscription_id"] == sub["id"]
    spent = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert spent["remaining_service_count"] == 0 and spent["status"] == "expired"
    assert spent.get("used_up_notice_sent_at") is not None
    assert await db.notifications.count_documents({"user_id": holder["customer_id"], "title": "All washes used"}) == 1

    user = await db.users.find_one({"_id": ObjectId(holder["customer_id"])})
    assert from_stored(user["last_completed_at"]).strftime("%Y-%m-%d %H:%M") == f"{yesterday} 10:30"
