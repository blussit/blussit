"""
The reminder loop (main._sweep_once) at production scale:

  - one failing sweep is logged and skipped; the rest of the pass runs;
  - the lease is renewed between sweeps (and inside long loops) and a pass
    stops the moment a renewal fails — another instance owns the sweeps;
  - the hourly repeat-booking nudge is hourly across instances, not per
    process (its last run lives on the lease doc);
  - the captain-reminder / late-to-start finders only look at a bounded
    scheduled_date window;
  - the repeat-booking batch skips cooled-down customers IN the query, so
    customers behind them are still reached;
  - pass expiry flips only the passes whose "has ended" note went out.

Every sweep that isn't under test is stubbed to a recorder so a pass never
touches Razorpay or other tests' rows.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app import main
from app.services import subscription_service
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.payment_service import PaymentService
from app.utils.timezone import now_ist

from tests.factories import make_customer

pytestmark = pytest.mark.asyncio

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
_PAYMENT_SYNCS = ("sync_pending_links", "sync_autopay_renewals", "sync_pending_manager_mandates")


def _stub_sweeps(monkeypatch, calls: list[str], keep: tuple[str, ...] = (), **overrides) -> None:
    """Every sweep records its name and finds nothing, unless kept real
    (`keep`) or replaced (`overrides`: BookingService method name -> async
    fn taking the method's args minus self)."""

    def recorder(name):
        async def fn(*_args, **_kwargs):
            calls.append(name)
            if name in overrides:
                return await overrides[name](*_args[1:], **_kwargs)
            return 0 if name in _PAYMENT_SYNCS or name == "_sweep_holds" else []

        return fn

    for name in ("_sweep_holds", *_FINDERS):
        if name not in keep:
            monkeypatch.setattr(BookingService, name, recorder(name))
    for name in _PAYMENT_SYNCS:
        monkeypatch.setattr(PaymentService, name, recorder(name))
    for name in ("find_subscriptions_expiring_soon", "find_subscriptions_ended", "find_passes_due_wash_reminder"):
        if name not in keep:
            monkeypatch.setattr(subscription_service, name, recorder(name))
    # Customer reminders only run 10 AM–7 PM IST; these tests are about the
    # loop's mechanics, so they hold whatever the wall clock says.
    monkeypatch.setattr(main, "_customer_hours_open", lambda: True)


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    holder = "holder-a"
    assert await main._claim_lease(db, holder)
    yield holder
    await db.locks.delete_many({"_id": main._LEASE_ID})


async def test_a_failing_sweep_does_not_stop_the_rest_of_the_pass(db, lease, monkeypatch):
    calls: list[str] = []

    async def boom(*_a, **_k):
        raise RuntimeError("simulated Mongo hiccup")

    _stub_sweeps(monkeypatch, calls, find_bookings_needing_reminder=boom)
    await main._sweep_once(db, lease)

    assert "find_bookings_needing_reminder" in calls
    # Every later sweep still ran — including the very last ones.
    for later in ("find_bookings_unassigned_too_long", "find_bookings_payment_expired", "sync_pending_links", "find_subscriptions_ended", "find_customers_due_repeat_reminder"):
        assert later in calls, f"{later} was skipped because an earlier sweep failed"


async def test_losing_the_lease_mid_pass_stops_it_at_once(db, lease, monkeypatch):
    calls: list[str] = []

    async def stolen(*_a, **_k):
        # Another instance takes over (our lease lapsed while we were slow).
        await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"holder": "holder-b"}})
        return []

    _stub_sweeps(monkeypatch, calls, find_bookings_needing_reminder=stolen)
    await main._sweep_once(db, lease)  # stops quietly, doesn't raise

    assert calls[-1] == "find_bookings_needing_reminder"
    assert "find_bookings_unassigned_too_long" not in calls
    assert "find_customers_due_repeat_reminder" not in calls
    assert (await db.locks.find_one({"_id": main._LEASE_ID}))["holder"] == "holder-b", "never renews someone else's lease"


async def test_lease_lost_inside_a_long_loop_stops_before_the_next_item(db, lease, monkeypatch):
    monkeypatch.setattr(main, "_ITEM_RENEW_SECONDS", 0)
    handled: list[int] = []

    async def handle(item):
        handled.append(item["n"])
        await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"holder": "holder-b"}})

    with pytest.raises(main._StopPass):
        await main._SweepPass(db, lease).each([{"n": 1}, {"n": 2}, {"n": 3}], "test", handle)
    assert handled == [1], "no customer is messaged once another instance owns the sweeps"


async def test_spent_budget_starts_no_new_sweep(db, lease, monkeypatch):
    monkeypatch.setattr(main, "_PASS_BUDGET_SECONDS", -1)
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    await main._sweep_once(db, lease)
    assert calls == []


async def test_repeat_sweep_is_hourly_across_lease_holders(db, lease, monkeypatch):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)

    await main._sweep_once(db, lease)
    assert calls.count("find_customers_due_repeat_reminder") == 1

    # holder-a dies; a fresh instance takes the lease minutes later.
    await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert await main._claim_lease(db, "holder-b")
    calls.clear()
    await main._sweep_once(db, "holder-b")
    assert "find_bookings_needing_reminder" in calls, "holder-b does run the minute sweeps"
    assert "find_customers_due_repeat_reminder" not in calls, "...but not the hourly one again"

    # An hour after the last run it's due again — for whoever holds the lease.
    await db.locks.update_one({"_id": main._LEASE_ID}, {"$set": {"repeat_sweep_at": datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)}})
    calls.clear()
    await main._sweep_once(db, "holder-b")
    assert calls.count("find_customers_due_repeat_reminder") == 1
    # The old holder can't claim it (or anything) any more.
    assert not await main._claim_repeat_sweep(db, lease)


def _midnight(days: int) -> datetime:
    return now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(days=days)


async def test_reminder_and_late_start_finders_ignore_stale_days(db, cleanup):
    tag = str(ObjectId())
    cleanup.append(("bookings", {"customer_id": {"$regex": f"^{tag}"}}))
    now = now_ist()
    base = {"status": "assigned", "captain_id": "cap-x", "service_center_id": "ctr-x", "scheduled_slot": "00:00-03:00", "is_deleted": False}

    # Captain "starting soon" ping: one starting in 20 minutes is due, last
    # week's never is. (NTF-06: neither is today's 00:00 slot by afternoon —
    # a slot that began over half an hour ago gets no "starting soon".)
    soon = now + timedelta(minutes=20)
    today = await db.bookings.insert_one({
        **base, "customer_id": f"{tag}-1", "booking_number": f"R-{tag}-T",
        "scheduled_date": datetime(soon.year, soon.month, soon.day), "scheduled_slot": f"{soon:%H:%M}-{soon + timedelta(hours=3):%H:%M}",
    })
    stale = await db.bookings.insert_one({**base, "customer_id": f"{tag}-2", "booking_number": f"R-{tag}-S", "scheduled_date": _midnight(-6)})
    due = {str(b["_id"]) for b in await BookingService(db).find_bookings_needing_reminder()}
    assert str(today.inserted_id) in due
    assert str(stale.inserted_id) not in due

    # Late to start: today's keeps nudging, a days-old one stops re-alerting.
    late = {**base, "reminder_sent": True, "assigned_at": now - timedelta(days=10)}
    late_today = await db.bookings.insert_one({**late, "customer_id": f"{tag}-3", "booking_number": f"L-{tag}-T", "scheduled_date": _midnight(0), "estimated_start_at": now - timedelta(hours=2)})
    late_stale = await db.bookings.insert_one({**late, "customer_id": f"{tag}-4", "booking_number": f"L-{tag}-S", "scheduled_date": _midnight(-5), "estimated_start_at": now - timedelta(days=5)})
    late_ids = {str(b["_id"]) for b in await BookingService(db).find_bookings_late_to_start()}
    assert str(late_today.inserted_id) in late_ids
    assert str(late_stale.inserted_id) not in late_ids


async def test_repeat_batch_skips_cooled_down_customers_so_later_ones_are_reached(db, cleanup):
    now = now_ist()
    bs = BookingService(db)

    async def customer(lapsed_days: int, **user_fields) -> str:
        cid = await make_customer(db)
        cleanup.append(("bookings", {"customer_id": cid}))
        cleanup.append(("users", {"_id": ObjectId(cid)}))
        # What every completion path stamps (BookingService._after_visit_completed).
        await db.users.update_one({"_id": ObjectId(cid)}, {"$set": {"last_completed_at": now - timedelta(days=lapsed_days), **user_fields}})
        # Lapsed far longer than anything other tests leave behind, so these
        # are the longest-lapsed customers in the database.
        await db.bookings.insert_one({
            "customer_id": cid, "status": "completed", "is_deleted": False, "booking_number": f"RR-{cid}",
            "completed_at": now - timedelta(days=lapsed_days), "scheduled_date": _midnight(-lapsed_days), "scheduled_slot": "09:00-12:00",
        })
        return cid

    cooling = [await customer(900 - i, last_repeat_reminder_at=now - timedelta(days=5)) for i in range(3)]
    opted_out = await customer(880, marketing_opt_out=True)
    booked = await customer(870)
    await db.bookings.insert_one({"customer_id": booked, "status": "pending", "is_deleted": False, "booking_number": f"RR-{booked}-live", "scheduled_date": _midnight(1), "scheduled_slot": "09:00-12:00"})
    on_pass = await customer(860)
    await db.user_subscriptions.insert_one({"customer_id": on_pass, "status": "active", "is_deleted": False, "end_date": datetime.now(timezone.utc) + timedelta(days=10)})
    cleanup.append(("user_subscriptions", {"customer_id": on_pass}))
    cooled_off = await customer(850, last_repeat_reminder_at=now - timedelta(days=45))
    fresh = await customer(840)

    # limit=1: the old "take 3x limit longest-lapsed, then filter" found only
    # the three cooling-down customers and returned nobody.
    first = await bs.find_customers_due_repeat_reminder(21, limit=1)
    assert [str(u["_id"]) for u in first] == [cooled_off]

    batch = {str(u["_id"]) for u in await bs.find_customers_due_repeat_reminder(21, limit=50)}
    assert {cooled_off, fresh} <= batch
    assert not batch & {*cooling, opted_out, booked, on_pass}

    # Once reminded, a customer drops out and the next one is reached.
    await bs.mark_repeat_reminder_sent(cooled_off)
    assert [str(u["_id"]) for u in await bs.find_customers_due_repeat_reminder(21, limit=1)] == [fresh]


async def test_pass_expiry_only_expires_passes_it_told(db, lease, monkeypatch, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    ended = datetime.now(timezone.utc) - timedelta(days=1)
    ids = [
        (await db.user_subscriptions.insert_one({"customer_id": customer_id, "status": "active", "is_deleted": False, "end_date": ended - timedelta(minutes=i)})).inserted_id
        for i in range(3)
    ]
    failing = str(ids[1])

    told: list[str] = []

    async def notify(self, user_id, title, message, *args, **kwargs):
        ref = args[1] if len(args) > 1 else kwargs.get("reference_id")
        told.append(ref)
        if ref == failing:
            raise RuntimeError("simulated notification failure")

    monkeypatch.setattr(NotificationService, "notify", notify)
    real_find = subscription_service.find_subscriptions_ended

    async def small_batches(db_, limit=100, exclude_ids=()):
        # Force paging (more ended passes than one batch holds), and keep
        # any other test's leftover passes out of this pass.
        others = await db_.user_subscriptions.distinct("_id", {"status": "active", "customer_id": {"$ne": customer_id}})
        return await real_find(db_, limit=2, exclude_ids=[*exclude_ids, *others])

    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, keep=("find_subscriptions_ended",))
    monkeypatch.setattr(subscription_service, "find_subscriptions_ended", small_batches)

    await main._sweep_once(db, lease)

    statuses = {str(s["_id"]): s["status"] for s in await db.user_subscriptions.find({"_id": {"$in": ids}}).to_list(length=10)}
    assert statuses == {str(ids[0]): "expired", failing: "active", str(ids[2]): "expired"}, "never flipped without its note"
    assert sorted(told) == sorted(str(i) for i in ids), "every ended pass was told, across batches"
    assert told.count(failing) == 1, "a failing one is retried next pass, not in a tight loop"

    # Next pass: the retry succeeds and only then is it expired.
    monkeypatch.setattr(NotificationService, "notify", lambda *a, **k: _noop())
    await main._sweep_once(db, lease)
    assert (await db.user_subscriptions.find_one({"_id": ids[1]}))["status"] == "expired"


async def _noop():
    return None
