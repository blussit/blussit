"""Reminder-loop resilience:

  FAIL-05  one slow sweep (slow WhatsApp sends, big backlog) is time-boxed on
           its own and can no longer eat the whole pass budget — the sweeps
           after it still run in the same pass.
  FAIL-07  every sweep call has a hard timeout BELOW the lease duration, so a
           hung sweep can't outlive the lease another instance may then take
           (double sends); the timeout is logged.
  FAIL-06  shutdown waits (bounded) for in-flight background work — startup
           tasks, background WhatsApp sends, the current sweep — before the
           HTTP clients and the database are closed.
"""
import asyncio
import logging
import time

import pytest

from app import main
from app.services import notification_service
from app.services.booking_service import BookingService

from tests.test_reminder_loop import _stub_sweeps

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    holder = "holder-fix-infra"
    assert await main._claim_lease(db, holder)
    yield holder
    await db.locks.delete_many({"_id": main._LEASE_ID})


def test_sweep_timeout_is_below_the_lease():
    assert main._SWEEP_TIMEOUT_SECONDS < main._LEASE_SECONDS
    assert main._SWEEP_BUDGET_SECONDS < main._PASS_BUDGET_SECONDS


async def test_a_slow_sweep_does_not_starve_the_later_sweeps(db, lease, monkeypatch):
    """The audit repro (FAIL-05): six late bookings, a 1 s send each. Before
    the fix the late-to-start sweep spent the whole pass budget and every
    later sweep was skipped."""
    monkeypatch.setattr(main, "_SWEEP_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(main, "_ITEM_RENEW_SECONDS", 0.5)
    handled: list[str] = []

    async def six_late(*_a, **_k):
        return [{"_id": f"late-{i}", "booking_number": f"BK-LATE-{i}"} for i in range(6)]

    async def slow_flag(self, booking):
        await asyncio.sleep(0.6)  # a slow Meta call
        handled.append(booking["_id"])

    async def mark(self, booking_id):
        return None

    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, find_bookings_late_to_start=six_late)
    monkeypatch.setattr(BookingService, "flag_late_to_start", slow_flag)
    monkeypatch.setattr(BookingService, "mark_late_start_reminder_sent", mark)

    started = time.monotonic()
    await main._sweep_once(db, lease)
    took = time.monotonic() - started

    assert 0 < len(handled) < 6, "the slow sweep stopped at its own budget (the rest wait for next pass)"
    for later in ("find_bookings_captain_not_reached", "find_subscriptions_expiring_soon", "find_subscriptions_ended", "find_customers_due_repeat_reminder"):
        assert later in calls, f"{later} was starved by the slow sweep"
    assert took < 5, took


async def test_a_hung_sweep_is_cut_off_below_the_lease_and_logged(db, lease, monkeypatch, caplog):
    monkeypatch.setattr(main, "_SWEEP_TIMEOUT_SECONDS", 0.3)
    caplog.set_level(logging.WARNING)

    async def hangs(*_a, **_k):
        await asyncio.sleep(3600)  # e.g. a socket that never answers

    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, find_bookings_needing_reminder=hangs)
    started = time.monotonic()
    await main._sweep_once(db, lease)
    assert time.monotonic() - started < 5
    assert "find_bookings_unassigned_too_long" in calls, "the pass carries on after the cut-off"
    assert any("timeout" in r.getMessage().lower() and "captain start reminders" in r.getMessage() for r in caplog.records)
    # The lease is still ours — the hung sweep never outlived it.
    assert (await db.locks.find_one({"_id": main._LEASE_ID}))["holder"] == lease


async def test_existing_pass_budget_still_stops_new_sweeps(db, lease, monkeypatch):
    monkeypatch.setattr(main, "_PASS_BUDGET_SECONDS", -1)
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    await main._sweep_once(db, lease)
    assert calls == []


async def test_a_stop_request_ends_the_pass_at_the_next_checkpoint(db, lease, monkeypatch):
    calls: list[str] = []
    stop = asyncio.Event()
    monkeypatch.setattr(main, "_stop_event", stop)

    async def first(*_a, **_k):
        stop.set()  # shutdown begins while this sweep runs
        return []

    _stub_sweeps(monkeypatch, calls, find_bookings_needing_reminder=first)
    await main._sweep_once(db, lease)
    assert calls[-1] == "find_bookings_needing_reminder"
    assert "find_bookings_unassigned_too_long" not in calls


# ----------------------------------------------------------- PAY-02 wiring
async def test_expiry_sweep_cancels_only_if_still_unpaid(db, lease, monkeypatch):
    """The sweep must ask the cancel to re-check "still unpaid" in its own
    write, for a single booking and for a multi-car visit."""
    from app.services.payment_service import PaymentService

    expired = [
        {"_id": "000000000000000000000001", "booking_number": "BK-EXP-1", "customer_id": "c1"},
        {"_id": "000000000000000000000002", "booking_number": "BK-EXP-2", "customer_id": "c2", "booking_group_id": "grp-1"},
    ]

    async def find_expired(*_a, **_k):
        return expired

    async def prepare(self, booking, window):
        return True

    seen: list[tuple[str, dict]] = []

    async def cancel_one(self, booking_id, payload, **kwargs):
        seen.append(("booking", kwargs))

    async def cancel_group(self, group_id, payload, **kwargs):
        seen.append(("group", kwargs))

    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, find_bookings_payment_expired=find_expired)
    monkeypatch.setattr(PaymentService, "prepare_expiry", prepare)
    monkeypatch.setattr(BookingService, "cancel_booking", cancel_one)
    monkeypatch.setattr(BookingService, "cancel_booking_group", cancel_group)
    await main._sweep_once(db, lease)

    assert [kind for kind, _ in seen] == ["booking", "group"]
    assert all(kwargs.get("only_if_unpaid") is True for _, kwargs in seen), seen


# ---------------------------------------------------------------- FAIL-06
@pytest.fixture
def no_real_close(monkeypatch):
    """on_shutdown closes the shared HTTP clients and Mongo — record the
    order instead, so the session database stays open for other tests."""
    order: list[str] = []

    async def close_clients():
        order.append("http_clients_closed")

    async def close_mongo():
        order.append("mongo_closed")

    from app.core import http_client

    monkeypatch.setattr(http_client, "close_shared_clients", close_clients)
    monkeypatch.setattr(main, "close_mongo_connection", close_mongo)
    monkeypatch.setattr(main, "_reminder_task", None)
    monkeypatch.setattr(main, "_stop_event", None)
    monkeypatch.setattr(main, "_startup_tasks", set())
    monkeypatch.setattr(main, "_maintenance_tasks", set())
    return order


async def test_shutdown_waits_for_in_flight_background_sends(db, no_real_close, monkeypatch):
    order = no_real_close

    async def send():
        await asyncio.sleep(0.3)
        order.append("whatsapp_sent")

    task = asyncio.create_task(send())
    notification_service._background_tasks.add(task)
    task.add_done_callback(notification_service._background_tasks.discard)

    async def init():
        await asyncio.sleep(0.2)
        order.append("deferred_init_done")

    main._track_startup_task(asyncio.create_task(init()))

    await main.on_shutdown()
    assert order.index("whatsapp_sent") < order.index("http_clients_closed")
    assert order.index("deferred_init_done") < order.index("http_clients_closed")
    assert order[-1] == "mongo_closed"


async def test_shutdown_is_bounded_when_a_task_hangs(db, no_real_close, monkeypatch):
    monkeypatch.setattr(main, "_SHUTDOWN_GRACE_SECONDS", 0.3)
    order = no_real_close

    async def hang():
        await asyncio.sleep(3600)

    task = asyncio.create_task(hang())
    notification_service._background_tasks.add(task)
    task.add_done_callback(notification_service._background_tasks.discard)

    started = time.monotonic()
    await main.on_shutdown()
    assert time.monotonic() - started < 3
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert order == ["http_clients_closed", "mongo_closed"]


async def test_shutdown_lets_the_current_sweep_finish_then_stops_the_loop(db, no_real_close, monkeypatch):
    """The loop is told to stop (not cancelled mid-send): it ends at its next
    checkpoint/sleep, inside the grace period."""
    order = no_real_close
    monkeypatch.setattr(main, "_stop_event", asyncio.Event())

    async def loop():
        while not main._stop_event.is_set():
            await main._sleep_unless_stopping(60)
        order.append("loop_stopped")

    monkeypatch.setattr(main, "_reminder_task", asyncio.create_task(loop()))
    await asyncio.sleep(0)
    started = time.monotonic()
    await main.on_shutdown()
    assert time.monotonic() - started < 2
    assert order[0] == "loop_stopped"


async def test_shutdown_stops_the_whatsapp_retry_loop_before_closing_clients(db, no_real_close, monkeypatch):
    order = no_real_close
    monkeypatch.setattr(main, "_stop_event", asyncio.Event())

    async def loop():
        while not main._stopping():
            await main._sleep_unless_stopping(30)
        order.append("retry_loop_stopped")

    main._start_maintenance(loop())
    await asyncio.sleep(0)
    await main.on_shutdown()
    assert order.index("retry_loop_stopped") < order.index("http_clients_closed")
    assert not main._maintenance_tasks


# ------------------------------------------- WhatsApp retry / template sync
async def test_retry_pass_runs_in_slices_until_the_queue_is_drained(db, monkeypatch):
    calls: list[dict] = []
    claims = iter([20, 20, 7, 0])

    async def retry_outbox(self, limit=100, time_budget_seconds=25.0):
        calls.append({"limit": limit, "budget": time_budget_seconds})
        return {"claimed": next(claims)}

    monkeypatch.setattr(notification_service.NotificationService, "retry_outbox", retry_outbox)
    monkeypatch.setattr(main, "_stop_event", None)
    await main._retry_whatsapp_pass(db)
    assert len(calls) == 4, "keeps slicing while rows come back, stops once nothing is claimed"
    assert all(c["budget"] <= main._RETRY_SLICE_SECONDS and c["limit"] <= main._RETRY_SLICE_ROWS for c in calls)


async def test_retry_pass_is_capped_at_its_row_budget(db, monkeypatch):
    async def retry_outbox(self, limit=100, time_budget_seconds=25.0):
        return {"claimed": limit}

    monkeypatch.setattr(notification_service.NotificationService, "retry_outbox", retry_outbox)
    seen: list[int] = []
    real = notification_service.NotificationService.retry_outbox

    async def counting(self, limit=100, time_budget_seconds=25.0):
        seen.append(limit)
        return await real(self, limit, time_budget_seconds)

    monkeypatch.setattr(notification_service.NotificationService, "retry_outbox", counting)
    await main._retry_whatsapp_pass(db)
    assert sum(seen) == main._RETRY_PASS_ROWS


async def test_a_hung_retry_slice_is_cut_off_and_logged(db, monkeypatch, caplog):
    monkeypatch.setattr(main, "_RETRY_SLICE_TIMEOUT_SECONDS", 0.2)
    caplog.set_level(logging.ERROR)

    async def hangs(self, limit=100, time_budget_seconds=25.0):
        await asyncio.sleep(3600)

    monkeypatch.setattr(notification_service.NotificationService, "retry_outbox", hangs)
    started = time.monotonic()
    await main._retry_whatsapp_pass(db)
    assert time.monotonic() - started < 3
    assert any("WhatsApp retry" in r.getMessage() and "timeout" in r.getMessage() for r in caplog.records)


async def test_retry_pass_stops_at_shutdown(db, monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(main, "_stop_event", stop)
    calls: list[int] = []

    async def retry_outbox(self, limit=100, time_budget_seconds=25.0):
        calls.append(limit)
        stop.set()
        return {"claimed": limit}

    monkeypatch.setattr(notification_service.NotificationService, "retry_outbox", retry_outbox)
    await main._retry_whatsapp_pass(db)
    assert len(calls) == 1


async def test_template_sync_asks_for_an_hourly_sync(db, monkeypatch):
    from app.services.whatsapp_crm_service import WhatsAppCrmService

    seen: list[int] = []

    async def due(self, max_age_minutes=60):
        seen.append(max_age_minutes)
        return {"skipped": True}

    monkeypatch.setattr(WhatsAppCrmService, "sync_templates_if_due", due)
    await main._sync_whatsapp_templates(db)
    assert seen == [60]


async def test_link_sweep_sends_no_free_text_receipt(db, lease, monkeypatch):
    """The receipt is the settle path's single templated send; the sweep
    must not pass a free-text callback any more."""
    from app.services.payment_service import PaymentService

    seen: list[tuple] = []
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)

    async def sync_links(self, *args, **kwargs):
        seen.append((args, kwargs))
        return 0

    monkeypatch.setattr(PaymentService, "sync_pending_links", sync_links)
    await main._sweep_once(db, lease)
    assert seen == [((), {})]
