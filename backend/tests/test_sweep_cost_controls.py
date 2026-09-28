"""
Pre-production cost controls on the reminder loop and outbound calls:

  - still-unassigned nudge: the first reminder goes to WhatsApp, repeats
    are in-app only and held outside the center's working hours, and only
    slots starting within a day are nudged at all;
  - the "captain missed the window" escalation fires once and a manager's
    Resolve sticks;
  - the late-to-start finder reads yesterday..tomorrow only;
  - the money sweeps run before the notification sweeps;
  - old payment links / pending mandates back off, and the captain's QR
    poll asks Razorpay about a link at most every ~10 s;
  - outbound HTTP reuses one pooled client, and completion / cancellation
    messages never hold up the request.

Every external call is stubbed; WhatsApp is the log provider (conftest).
"""
import asyncio
from datetime import timedelta

import httpx
import pytest
from bson import ObjectId

from app import main
from app.core import http_client
from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCancelRequest, BookingCreateRequest, PhotoCaptureRequest
from app.services import payment_service, subscription_service
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.payment_service import PaymentService
from app.services.whatsapp_service import MetaCloudWhatsAppProvider
from app.utils.timezone import from_stored, now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_captain,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
)

pytestmark = pytest.mark.asyncio


def _midnight(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _hours_around_now(opens_in_min: int, closes_in_min: int) -> tuple[str, str]:
    at = now_ist()
    now_min = at.hour * 60 + at.minute

    def hhmm(minutes: int) -> str:
        minutes %= 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return hhmm(now_min + opens_in_min), hhmm(now_min + closes_in_min)


@pytest.fixture
async def center(db, cleanup):
    center_id = await make_service_center(db)
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    manager = await db.users.find_one({"_id": ObjectId(manager_id)})
    cleanup.append(("bookings", {"service_center_id": center_id}))
    cleanup.append(("booking_status_history", {"changed_by": "mgr-test"}))
    cleanup.append(("notifications", {"user_id": manager_id}))
    cleanup.append(("whatsapp_outbox", {"phone": manager["phone"]}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    return {"id": center_id, "manager_id": manager_id, "manager_phone": manager["phone"]}


async def _set_hours(db, center: dict, opens: str, closes: str) -> None:
    await db.service_centers.update_one(
        {"_id": ObjectId(center["id"])}, {"$set": {"working_hours_start": opens, "working_hours_end": closes}}
    )


async def _unassigned(db, center: dict, starts_in: timedelta) -> str:
    now = now_ist()
    start = now + starts_in
    result = await db.bookings.insert_one({
        "status": "pending", "service_center_id": center["id"], "customer_id": f"cust-{ObjectId()}",
        "booking_number": f"UN-{ObjectId()}", "is_deleted": False, "issue_flag": None,
        "scheduled_date": _midnight(start), "scheduled_slot": "09:00-12:00",
        "slot_start": start, "slot_end": start + timedelta(hours=3),
        "awaiting_assignment_since": now - timedelta(hours=1),
    })
    return str(result.inserted_id)


# ------------------------------------------------------------ the pass


_FINDERS = (
    "find_bookings_needing_reminder",
    "find_bookings_unassigned_too_long",
    "find_bookings_late_to_start",
    "find_bookings_captain_not_reached",
    "find_bookings_stuck_on_the_way",
    "find_bookings_service_overrunning",
    "find_bookings_idle_after_arrival",
    "find_bookings_captain_left_site",
    "find_bookings_payment_expired",
    "find_bookings_payment_reminder_due",
    "find_customers_due_repeat_reminder",
)
_PAYMENT_SYNCS = ("sync_pending_orders", "sync_pending_links", "sync_autopay_renewals", "sync_pending_manager_mandates")


def _stub_pass(monkeypatch, calls: list[str], **overrides) -> None:
    """Every sweep records its name and finds nothing, unless `overrides`
    names a replacement (BookingService method -> async fn(service))."""

    def recorder(name):
        async def fn(*args, **kwargs):
            calls.append(name)
            if name in overrides:
                return await overrides[name](args[0])
            return 0 if name in _PAYMENT_SYNCS or name == "_sweep_holds" else []

        return fn

    for name in ("_sweep_holds", *_FINDERS):
        monkeypatch.setattr(BookingService, name, recorder(name))
    for name in _PAYMENT_SYNCS:
        monkeypatch.setattr(PaymentService, name, recorder(name))
    for name in ("find_subscriptions_expiring_soon", "find_subscriptions_ended", "find_passes_due_wash_reminder"):
        monkeypatch.setattr(subscription_service, name, recorder(name))
    # Customer reminders only run 10 AM–7 PM IST — hold the window open.
    monkeypatch.setattr(main, "_customer_hours_open", lambda: True)


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    assert await main._claim_lease(db, "holder-cost")
    yield "holder-cost"
    await db.locks.delete_many({"_id": main._LEASE_ID})


async def test_money_sweeps_run_before_the_notification_sweeps(db, lease, monkeypatch):
    calls: list[str] = []
    _stub_pass(monkeypatch, calls)
    await main._sweep_once(db, lease)

    money = ["sync_pending_orders", "find_bookings_payment_expired", "find_bookings_payment_reminder_due",
             "sync_pending_links", "sync_autopay_renewals", "sync_pending_manager_mandates"]
    notices = ["find_bookings_needing_reminder", "find_bookings_unassigned_too_long", "find_bookings_late_to_start",
               "find_bookings_captain_not_reached", "find_bookings_captain_left_site", "find_subscriptions_expiring_soon"]
    assert all(name in calls for name in money + notices)
    assert max(calls.index(n) for n in money) < min(calls.index(n) for n in notices)


# ------------------------------------------------------- unassigned nudge


async def test_unassigned_nudge_only_for_slots_within_a_day(db, center):
    near = await _unassigned(db, center, timedelta(hours=3))
    far = await _unassigned(db, center, timedelta(hours=30))
    due = {str(b["_id"]): b for b in await BookingService(db).find_bookings_unassigned_too_long()}
    assert near in due and due[near]["_first_unassigned_reminder"] is True
    assert far not in due


async def test_first_unassigned_reminder_is_whatsapp_repeats_in_app_only_in_working_hours(db, center, lease, monkeypatch):
    booking_id = await _unassigned(db, center, timedelta(hours=3))
    await _set_hours(db, center, *_hours_around_now(-60, 60))
    real_finder = BookingService.find_bookings_unassigned_too_long

    async def only_ours(service):
        return [b for b in await real_finder(service) if str(b["_id"]) == booking_id]

    calls: list[str] = []
    _stub_pass(monkeypatch, calls, find_bookings_unassigned_too_long=only_ours)

    async def counts() -> tuple[int, int]:
        in_app = await db.notifications.count_documents({"user_id": center["manager_id"], "reference_id": booking_id})
        whatsapp = await db.whatsapp_outbox.count_documents({"phone": center["manager_phone"]})
        return in_app, whatsapp

    async def fifteen_minutes_pass() -> None:
        await db.bookings.update_one(
            {"_id": ObjectId(booking_id)}, {"$set": {"unassigned_reminder_sent_at": now_ist() - timedelta(minutes=16)}}
        )

    await main._sweep_once(db, lease)
    assert await counts() == (1, 0), "in-app only — a manager's WhatsApp carries new bookings only"

    await main._sweep_once(db, lease)
    assert await counts() == (1, 0), "throttled to once per interval"

    await fifteen_minutes_pass()
    await main._sweep_once(db, lease)
    assert await counts() == (2, 0), "a repeat is in-app only"

    # Center closed: no repeat at all...
    await _set_hours(db, center, *_hours_around_now(120, 180))
    await fifteen_minutes_pass()
    await main._sweep_once(db, lease)
    assert await counts() == (2, 0)

    # ...and the first pass after opening catches up.
    await _set_hours(db, center, *_hours_around_now(-60, 60))
    await main._sweep_once(db, lease)
    assert await counts() == (3, 0)

    # A new wait (the booking went back to the queue after the last
    # reminder) starts a fresh reminder — still in-app only for a manager.
    await db.bookings.update_one(
        {"_id": ObjectId(booking_id)},
        {"$set": {"awaiting_assignment_since": now_ist() - timedelta(minutes=20), "unassigned_reminder_sent_at": now_ist() - timedelta(minutes=30)}},
    )
    await main._sweep_once(db, lease)
    assert await counts() == (4, 0)


# --------------------------------------------------- captain not started


async def test_late_start_first_alert_is_whatsapp_repeats_in_app_only(db, center, cleanup, lease, monkeypatch):
    captain_id = await make_captain(db, center["id"])
    captain_phone = (await db.users.find_one({"_id": ObjectId(captain_id)}))["phone"]
    for coll, flt in [("users", {"_id": ObjectId(captain_id)}), ("captain_wallets", {"captain_id": captain_id}),
                      ("notifications", {"user_id": captain_id}), ("whatsapp_outbox", {"phone": captain_phone})]:
        cleanup.append((coll, flt))
    now = now_ist()
    result = await db.bookings.insert_one({
        "status": "assigned", "captain_id": captain_id, "service_center_id": center["id"], "customer_id": f"cust-{ObjectId()}",
        "booking_number": f"NS-{ObjectId()}", "is_deleted": False, "scheduled_date": _midnight(now), "scheduled_slot": "00:00-03:00",
        "duration_minutes": 40, "estimated_start_at": now - timedelta(hours=1), "assigned_at": now - timedelta(hours=2),
    })
    booking_id = str(result.inserted_id)
    real_finder = BookingService.find_bookings_late_to_start

    async def only_ours(service):
        return [b for b in await real_finder(service) if str(b["_id"]) == booking_id]

    _stub_pass(monkeypatch, [], find_bookings_late_to_start=only_ours)

    async def counts() -> dict:
        return {
            "manager_in_app": await db.notifications.count_documents({"user_id": center["manager_id"], "reference_id": booking_id}),
            "manager_whatsapp": await db.whatsapp_outbox.count_documents({"phone": center["manager_phone"]}),
            "captain_in_app": await db.notifications.count_documents({"user_id": captain_id, "reference_id": booking_id}),
            "captain_whatsapp": await db.whatsapp_outbox.count_documents({"phone": captain_phone}),
        }

    async def minutes_pass(minutes: int) -> None:
        doc = await db.bookings.find_one({"_id": result.inserted_id})
        await db.bookings.update_one({"_id": result.inserted_id}, {"$set": {
            "late_start_reminder_sent_at": from_stored(doc["late_start_reminder_sent_at"]) - timedelta(minutes=minutes),
            "issue_flagged_at": from_stored(doc["issue_flagged_at"]) - timedelta(minutes=minutes),
        }})

    await main._sweep_once(db, lease)
    # Manager: in-app only (WhatsApp is for new bookings); captain: first nudge on WhatsApp.
    assert await counts() == {"manager_in_app": 1, "manager_whatsapp": 0, "captain_in_app": 1, "captain_whatsapp": 1}

    await minutes_pass(6)
    await main._sweep_once(db, lease)
    assert await counts() == {"manager_in_app": 1, "manager_whatsapp": 0, "captain_in_app": 2, "captain_whatsapp": 1}, \
        "the captain's nudge keeps its cadence in-app; the manager isn't re-pinged every 5 minutes"

    await minutes_pass(10)
    await main._sweep_once(db, lease)
    assert await counts() == {"manager_in_app": 2, "manager_whatsapp": 0, "captain_in_app": 3, "captain_whatsapp": 1}

    # The captain heads out: nothing more for anyone.
    await db.bookings.update_one({"_id": result.inserted_id}, {"$set": {"status": "captain_on_the_way"}})
    await minutes_pass(20)
    await main._sweep_once(db, lease)
    assert await counts() == {"manager_in_app": 2, "manager_whatsapp": 0, "captain_in_app": 3, "captain_whatsapp": 1}


async def test_late_start_goes_quiet_for_everyone_after_a_resolve(db, center, cleanup):
    captain_id = await make_captain(db, center["id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    now = now_ist()
    result = await db.bookings.insert_one({
        "status": "assigned", "captain_id": captain_id, "service_center_id": center["id"], "customer_id": f"cust-{ObjectId()}",
        "booking_number": f"NR-{ObjectId()}", "is_deleted": False, "scheduled_date": _midnight(now), "scheduled_slot": "00:00-03:00",
        "duration_minutes": 40, "estimated_start_at": now - timedelta(hours=1), "assigned_at": now - timedelta(hours=2),
    })
    bs = BookingService(db)
    await bs.flag_late_to_start(await db.bookings.find_one({"_id": result.inserted_id}))
    await bs.resolve_issue(str(result.inserted_id), "mgr-test", "Spoke to the captain", "manager", center["id"])
    await bs.flag_late_to_start(await db.bookings.find_one({"_id": result.inserted_id}))
    assert await db.notifications.count_documents({"user_id": captain_id, "reference_id": str(result.inserted_id)}) == 1
    assert await db.notifications.count_documents({"user_id": center["manager_id"], "reference_id": str(result.inserted_id)}) == 1


# ------------------------------------------------- captain missed window


async def test_missed_window_escalates_once_and_a_resolve_sticks(db, center):
    now = now_ist()
    result = await db.bookings.insert_one({
        "status": "assigned", "captain_id": "cap-missed", "service_center_id": center["id"], "customer_id": f"cust-{ObjectId()}",
        "booking_number": f"MW-{ObjectId()}", "is_deleted": False, "scheduled_date": _midnight(now - timedelta(days=1)),
        "scheduled_slot": "09:00-12:00", "duration_minutes": 40,
        "estimated_start_at": now - timedelta(days=1), "assigned_at": now - timedelta(days=1, hours=1),
    })
    booking_id = str(result.inserted_id)
    bs = BookingService(db)

    async def urgent() -> int:
        return await db.notifications.count_documents({"user_id": center["manager_id"], "reference_id": booking_id, "title": {"$regex": "Urgent"}})

    await bs.flag_late_to_start(await db.bookings.find_one({"_id": result.inserted_id}))
    assert (await db.bookings.find_one({"_id": result.inserted_id}))["issue_flag"] == "captain_missed_window"
    assert await urgent() == 1

    # Every later nudge while it's still open: no new urgent alert.
    for _ in range(3):
        await bs.flag_late_to_start(await db.bookings.find_one({"_id": result.inserted_id}))
    assert await urgent() == 1

    await bs.resolve_issue(booking_id, "mgr-test", "Calling the customer to reschedule", "manager", center["id"])
    await bs.flag_late_to_start(await db.bookings.find_one({"_id": result.inserted_id}))
    after = await db.bookings.find_one({"_id": result.inserted_id})
    assert after["issue_flag"] is None and after["issue_resolved"] is True, "the manager's Resolve is not undone"
    assert await urgent() == 1


async def test_late_to_start_finder_ignores_days_after_tomorrow(db, center):
    now = now_ist()
    base = {
        "status": "assigned", "captain_id": "cap-late", "service_center_id": center["id"], "scheduled_slot": "00:00-03:00",
        "is_deleted": False, "reminder_sent": True, "assigned_at": now - timedelta(days=10),
        # Made-up past start: only the query bound can keep the far one out.
        "estimated_start_at": now - timedelta(hours=2),
    }
    today = await db.bookings.insert_one({**base, "customer_id": f"cust-{ObjectId()}", "booking_number": f"LT-{ObjectId()}", "scheduled_date": _midnight(now)})
    later = await db.bookings.insert_one({**base, "customer_id": f"cust-{ObjectId()}", "booking_number": f"LT-{ObjectId()}", "scheduled_date": _midnight(now + timedelta(days=3))})
    due = {str(b["_id"]) for b in await BookingService(db).find_bookings_late_to_start()}
    assert str(today.inserted_id) in due
    assert str(later.inserted_id) not in due


# ------------------------------------------------ Razorpay polling budget


class _Links:
    def __init__(self):
        self.fetched: list[str] = []

    def fetch(self, link_id):
        self.fetched.append(link_id)
        return {"id": link_id, "status": "created", "payments": []}


class _Mandates:
    def __init__(self):
        self.fetched: list[str] = []

    def fetch(self, sub_id):
        self.fetched.append(sub_id)
        return {"id": sub_id, "status": "created", "paid_count": 0}


class _Client:
    def __init__(self):
        self.payment_link = _Links()
        self.subscription = _Mandates()


@pytest.fixture
def rzp(monkeypatch):
    client = _Client()
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: client)
    monkeypatch.setattr(payment_service, "_sweeps_paused_until", 0.0)
    return client


async def test_old_payment_links_back_off_fresh_ones_stay_fast(db, cleanup, rzp):
    tag = f"cost-{ObjectId()}"
    cleanup.append(("payment_orders", {"customer_id": tag}))
    now = now_ist()
    base = {"kind": "link", "status": "created", "purpose": "subscription", "customer_id": tag, "amount_paise": 10000}
    await db.payment_orders.insert_many([
        {**base, "razorpay_link_id": f"plink_old_{tag}", "created_at": now - timedelta(hours=3)},
        {**base, "razorpay_link_id": f"plink_new_{tag}", "created_at": now - timedelta(minutes=1)},
    ])
    old, new = f"plink_old_{tag}", f"plink_new_{tag}"
    svc = PaymentService(db)

    for _ in range(3):
        await svc.sync_pending_links(lambda _o: None)
    assert rzp.payment_link.fetched.count(new) == 3, "a link the customer may be paying right now is checked every pass"
    assert rzp.payment_link.fetched.count(old) == 1, "an hours-old one waits for its backoff"
    doc = await db.payment_orders.find_one({"razorpay_link_id": old})
    assert doc["checks"] == 1 and doc["next_check_at"] is not None

    # Its turn comes round again — and the next wait is longer.
    await db.payment_orders.update_one({"razorpay_link_id": old}, {"$set": {"next_check_at": now_ist() - timedelta(seconds=1)}})
    await svc.sync_pending_links(lambda _o: None)
    assert rzp.payment_link.fetched.count(old) == 2
    doc = await db.payment_orders.find_one({"razorpay_link_id": old})
    assert doc["checks"] == 2
    wait = (from_stored(doc["next_check_at"]) - now_ist()).total_seconds()
    assert 3 * 60 < wait <= 4 * 60 + 5


async def test_old_pending_mandate_backs_off_until_the_webhook_nudges_it(db, cleanup, rzp):
    tag = f"cost-{ObjectId()}"
    cleanup.append(("payment_orders", {"customer_id": tag}))
    mandate = f"sub_{tag}"
    await db.payment_orders.insert_one({
        "kind": "autopay", "status": "created", "purpose": "subscription", "customer_id": tag,
        "razorpay_subscription_id": mandate, "amount_paise": 50000, "created_at": now_ist() - timedelta(days=2),
    })
    svc = PaymentService(db)
    await svc.sync_pending_manager_mandates()
    await svc.sync_pending_manager_mandates()
    assert rzp.subscription.fetched.count(mandate) == 1

    assert await svc._dispatch_webhook("subscription.activated", {"subscription": {"entity": {"id": mandate}}}) == "mandate_nudged"
    await svc.sync_pending_manager_mandates()
    assert rzp.subscription.fetched.count(mandate) == 2, "the webhook puts it straight back in line"


async def test_captain_qr_poll_asks_razorpay_at_most_every_ten_seconds(db, cleanup, rzp):
    captain_id = await make_captain(db)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    booking = await db.bookings.insert_one({
        "status": "completed", "captain_id": captain_id, "customer_id": f"cust-{ObjectId()}", "booking_number": f"QR-{ObjectId()}",
        "payment_status": "pending", "total_amount": 499.0, "is_deleted": False,
    })
    booking_id = str(booking.inserted_id)
    cleanup.append(("bookings", {"_id": booking.inserted_id}))
    link_id = f"plink_qr_{booking_id}"
    await db.payment_orders.insert_one({
        "kind": "link", "status": "created", "purpose": "booking", "booking_id": booking_id, "customer_id": f"qr-{booking_id}",
        "razorpay_link_id": link_id, "amount_paise": 49900, "created_at": now_ist(),
    })
    cleanup.append(("payment_orders", {"razorpay_link_id": link_id}))
    svc = PaymentService(db)

    for _ in range(3):  # the modal polls every 4 s
        assert (await svc.captain_check_payment(booking_id, captain_id))["payment_status"] == "pending"
    assert rzp.payment_link.fetched.count(link_id) == 1

    await db.payment_orders.update_one({"razorpay_link_id": link_id}, {"$set": {"last_checked_at": now_ist() - timedelta(seconds=11)}})
    await svc.captain_check_payment(booking_id, captain_id)
    assert rzp.payment_link.fetched.count(link_id) == 2


# ---------------------------------------------------------- outbound HTTP


async def test_meta_sends_reuse_one_pooled_client(db, cleanup, monkeypatch):
    built: list[dict] = []

    class _Response:
        status_code = 200
        text = '{"messages": [{"id": "wamid.POOL"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.POOL"}]}

    class _PooledClient:
        def __init__(self, *args, **kwargs):
            built.append(kwargs)
            self.posted: list[str] = []

        async def post(self, url, **kwargs):
            self.posted.append(url)
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _PooledClient)
    phone = "9876500931"
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    provider = MetaCloudWhatsAppProvider(db, "token", "1234", "v20.0")
    assert await provider.send(phone, "one")
    assert await MetaCloudWhatsAppProvider(db, "token", "1234", "v20.0").send(phone, "two")
    assert len(built) == 1, "one client (one TLS pool) for every Meta call, not one per message"


async def test_shared_client_is_reused_and_recreated_after_shutdown():
    first = http_client.shared_client("cost-test", 5)
    assert http_client.shared_client("cost-test", 5) is first
    assert http_client.shared_client("cost-test-other", 5) is not first
    await http_client.close_shared_clients()
    assert first.is_closed
    again = http_client.shared_client("cost-test", 5)
    assert again is not first and not again.is_closed
    await http_client.close_shared_clients()


# ----------------------------------------- messages never hold a request


@pytest.fixture
async def job(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    service = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59", slot_duration_minutes=180)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, flt in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                      ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                      ("notifications", {"user_id": customer_id})]:
        cleanup.append((coll, flt))
    bs = BookingService(db)
    booking = await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[service],
        scheduled_date=now_ist() + timedelta(days=1), scheduled_slot="09:00-12:00",
    ))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=captain_id), "system", "admin", None)
    return {"id": booking["id"], "customer_id": customer_id, "captain_id": captain_id}


@pytest.fixture
def stalled_meta(monkeypatch):
    """WhatsApp sends that hang until released — any request that awaits
    one times out below."""
    gate = asyncio.Event()
    started: list[str] = []

    async def hang(self, user_id, *args, **kwargs):
        started.append(user_id)
        await gate.wait()

    monkeypatch.setattr(NotificationService, "_send_whatsapp", hang)
    yield started
    gate.set()


async def test_cancel_returns_without_waiting_on_whatsapp(db, job, stalled_meta):
    result = await asyncio.wait_for(
        BookingService(db).cancel_booking(job["id"], BookingCancelRequest(reason="Customer called off"), "admin-x", "admin"),
        timeout=5,
    )
    assert result["status"] == "cancelled"
    await asyncio.sleep(0.05)
    assert {job["customer_id"], job["captain_id"]} <= set(stalled_meta), "both messages still go out, just in the background"
    assert await db.notifications.count_documents({"user_id": job["customer_id"], "title": "Booking cancelled"}) == 1


async def test_after_photo_completion_returns_without_waiting_on_whatsapp(db, job, stalled_meta):
    now = now_ist()
    await db.bookings.update_one(
        {"_id": ObjectId(job["id"])},
        {"$set": {"status": "service_started", "vehicle_verified": True, "heading_at": now, "service_started_at": now}},
    )
    photo = PhotoCaptureRequest(image_url="https://example.com/after.jpg", latitude=22.7, longitude=75.8)
    result = await asyncio.wait_for(
        BookingService(db).capture_after_photo_and_complete(job["id"], photo, job["captain_id"]), timeout=5
    )
    assert result["status"] == "completed"
    await asyncio.sleep(0.05)
    assert job["customer_id"] in stalled_meta
    assert await db.notifications.count_documents({"user_id": job["customer_id"], "title": "Service completed"}) == 1
