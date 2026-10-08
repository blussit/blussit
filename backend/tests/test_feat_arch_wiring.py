"""ARCH 2026-10-07 — worker wiring of other agents' jobs (feature plan 2).

  - NOTIFY's NotificationService.send_review_requests() runs in the sweep
    pass: daytime only, at most every 10 minutes across instances, with the
    pass's checkpoint (sweep budget / lease) when it accepts one;
  - BOOKING's pre-slot customer reminder (find_bookings_needing_customer_
    reminder + send_customer_reminder) runs per booking under the pass's
    per-item checkpoint, one bad row never stopping the rest;
  - both are skipped silently while a build doesn't have them;
  - optional idempotent boot migrations (MONEY's open charges -> wallet,
    BOOKING's address snapshots) run when present, are skipped when absent,
    and a failure is logged, never raised.
"""
import logging
import sys
import types

import pytest

from app import main
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService

from tests.test_reminder_loop import _stub_sweeps

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    holder = "holder-feat-arch"
    assert await main._claim_lease(db, holder)
    yield holder
    await db.locks.delete_many({"_id": main._LEASE_ID})


@pytest.fixture
def no_new_sweeps(monkeypatch):
    """Start from 'not in this build' whatever the other agents have landed."""
    monkeypatch.delattr(NotificationService, "send_review_requests", raising=False)
    monkeypatch.delattr(BookingService, "find_bookings_needing_customer_reminder", raising=False)
    monkeypatch.delattr(BookingService, "send_customer_reminder", raising=False)


# --------------------------------------------------------- review requests
async def test_review_requests_run_paced_and_in_the_day(db, lease, monkeypatch, no_new_sweeps):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    seen: list[dict] = []

    async def send_review_requests(self, checkpoint=None):
        seen.append({"checkpoint": checkpoint})
        return {"sent": 2}

    monkeypatch.setattr(NotificationService, "send_review_requests", send_review_requests, raising=False)
    await main._sweep_once(db, lease)
    await main._sweep_once(db, lease)  # within 10 minutes: not again
    assert len(seen) == 1
    assert callable(seen[0]["checkpoint"]), "gets the pass checkpoint (budget + lease)"
    # Ten minutes later it runs again.
    from datetime import datetime, timedelta, timezone

    await db.locks.update_one(
        {"_id": main._LEASE_ID}, {"$set": {"review_request_sweep_at": datetime.now(timezone.utc) - timedelta(minutes=11)}}
    )
    await main._sweep_once(db, lease)
    assert len(seen) == 2


async def test_review_requests_wait_for_daytime(db, lease, monkeypatch, no_new_sweeps):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    monkeypatch.setattr(main, "_customer_hours_open", lambda: False)
    seen: list[int] = []

    async def send_review_requests(self):
        seen.append(1)

    monkeypatch.setattr(NotificationService, "send_review_requests", send_review_requests, raising=False)
    await main._sweep_once(db, lease)
    assert seen == []
    assert not (await db.locks.find_one({"_id": main._LEASE_ID})).get("review_request_sweep_at"), "no stamp burnt at night"


async def test_review_sweep_without_checkpoint_parameter_is_called_plainly(db, lease, monkeypatch, no_new_sweeps):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    seen: list[int] = []

    async def send_review_requests(self):
        seen.append(1)

    monkeypatch.setattr(NotificationService, "send_review_requests", send_review_requests, raising=False)
    await main._sweep_once(db, lease)
    assert seen == [1]


async def test_review_sweep_with_its_own_keywords_gets_none_it_does_not_declare(db, lease, monkeypatch, no_new_sweeps):
    """NOTIFY's real signature is (now=None, limit=...): no checkpoint is
    forced on it, and its own defaults stay in charge."""
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    seen: list[dict] = []

    async def send_review_requests(self, now=None, limit=50):
        seen.append({"now": now, "limit": limit})
        return {"sent": 0, "skipped": 0}

    monkeypatch.setattr(NotificationService, "send_review_requests", send_review_requests, raising=False)
    await main._sweep_once(db, lease)
    assert seen == [{"now": None, "limit": 50}]


async def test_a_failing_review_sweep_does_not_stop_the_pass(db, lease, monkeypatch, no_new_sweeps):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)

    async def boom(self, checkpoint=None):
        raise RuntimeError("Meta down")

    monkeypatch.setattr(NotificationService, "send_review_requests", boom, raising=False)
    await main._sweep_once(db, lease)
    assert "find_customers_due_repeat_reminder" in calls, "the sweeps after it still ran"


# ------------------------------------------------- pre-slot customer reminder
async def test_pre_slot_reminders_are_sent_per_booking(db, lease, monkeypatch, no_new_sweeps):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    due = [{"_id": f"b{i}", "booking_number": f"BK-PRE-{i}"} for i in range(3)]
    sent: list[str] = []

    async def finder(self):
        return due

    async def sender(self, booking):
        if booking["_id"] == "b1":
            raise RuntimeError("one bad row")
        sent.append(booking["_id"])

    monkeypatch.setattr(BookingService, "find_bookings_needing_customer_reminder", finder, raising=False)
    monkeypatch.setattr(BookingService, "send_customer_reminder", sender, raising=False)
    await main._sweep_once(db, lease)
    assert sent == ["b0", "b2"], "one failing booking is logged and skipped"
    assert "find_bookings_unassigned_too_long" in calls, "the pass carries on"


async def test_pre_slot_reminder_respects_the_sweep_budget(db, lease, monkeypatch, no_new_sweeps):
    import asyncio

    monkeypatch.setattr(main, "_SWEEP_BUDGET_SECONDS", 0.3)
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    sent: list[str] = []

    async def finder(self):
        return [{"_id": f"b{i}", "booking_number": f"BK-{i}"} for i in range(20)]

    async def slow(self, booking):
        await asyncio.sleep(0.1)
        sent.append(booking["_id"])

    monkeypatch.setattr(BookingService, "find_bookings_needing_customer_reminder", finder, raising=False)
    monkeypatch.setattr(BookingService, "send_customer_reminder", slow, raising=False)
    await main._sweep_once(db, lease)
    assert 0 < len(sent) < 20, "stops at its own budget; the rest are still due next pass"
    assert "find_customers_due_repeat_reminder" in calls


async def test_absent_sweeps_are_skipped_silently(db, lease, monkeypatch, no_new_sweeps, caplog):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    with caplog.at_level(logging.ERROR):
        await main._sweep_once(db, lease)
    assert not [r for r in caplog.records if "review" in r.getMessage().lower() or "pre-slot" in r.getMessage().lower()]
    assert "find_customers_due_repeat_reminder" in calls


# ------------------------------------------------------ optional boot tasks
@pytest.fixture
def fake_module(monkeypatch):
    def make(name: str, **attrs) -> None:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    return make


async def test_optional_boot_task_runs_the_first_one_present(db, fake_module, caplog):
    seen: list = []

    async def migrate(database):
        seen.append(database)
        return {"migrated": 3}

    fake_module("tests._arch_fake_money", migrate_open_charges_to_wallet=migrate)
    candidates = (("tests._arch_missing_module", "nope"), ("tests._arch_fake_money", "migrate_open_charges_to_wallet"))
    with caplog.at_level(logging.INFO, logger="app.main"):
        await main._run_optional_boot_task("open charges -> customer wallet", candidates, db)
    assert seen == [db]
    assert any("migrated" in r.getMessage() for r in caplog.records)


async def test_optional_boot_task_absent_is_skipped(db, caplog):
    with caplog.at_level(logging.INFO, logger="app.main"):
        await main._run_optional_boot_task("x", (("tests._arch_missing_module", "nope"),), db)
    assert any("not in this build" in r.getMessage() for r in caplog.records)


async def test_optional_boot_task_failure_is_logged_not_raised(db, fake_module, caplog):
    def broken(database):  # a sync one works too
        raise RuntimeError("half-migrated")

    fake_module("tests._arch_fake_booking", backfill_address_snapshots=broken)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        await main._run_optional_boot_task("booking address snapshots", (("tests._arch_fake_booking", "backfill_address_snapshots"),), db)
    assert any("retried next boot" in r.getMessage() for r in caplog.records)


async def test_the_boot_tasks_cover_money_and_booking_migrations():
    labels = [label for label, _ in main._OPTIONAL_BOOT_TASKS]
    assert any("wallet" in label for label in labels)
    assert any("address" in label for label in labels)
    names = {attr for _, candidates in main._OPTIONAL_BOOT_TASKS for _, attr in candidates}
    assert "migrate_open_charges_to_wallet" in names


async def test_deferred_init_runs_indexes_then_the_boot_tasks(db, monkeypatch):
    """Order matters: the unique indexes (e.g. a ledger idempotency key)
    exist before any migration writes through them."""
    from app.core import database

    order: list[str] = []

    async def indexes():
        order.append("indexes")

    async def task(label, candidates, database_):
        order.append(label)

    async def sync(*_a, **_k):
        order.append("template_sync")

    async def backfill(*_a, **_k):
        order.append("backfill")

    from app.services import auth_service, booking_service
    from app.services.coupon_service import CouponService

    monkeypatch.setattr(database, "create_indexes", indexes)
    monkeypatch.setattr(auth_service, "assign_missing_employee_ids", backfill)
    monkeypatch.setattr(CouponService, "ensure_default_launch_offer", backfill)
    monkeypatch.setattr(booking_service, "backfill_last_completed_at", backfill)
    monkeypatch.setattr(booking_service, "backfill_seat_ownership", backfill)
    monkeypatch.setattr(main, "_run_optional_boot_task", task)
    monkeypatch.setattr(main, "_sync_whatsapp_templates", sync)
    monkeypatch.setattr(main, "_stop_event", None)
    await main._deferred_init()
    assert order[0] == "indexes"
    assert order[-1] == "template_sync"
    assert order.count("backfill") == 4
    assert [o for o in order if o not in ("indexes", "template_sync", "backfill")] == [label for label, _ in main._OPTIONAL_BOOT_TASKS]
    assert order.index("backfill") < order.index(main._OPTIONAL_BOOT_TASKS[0][0])
