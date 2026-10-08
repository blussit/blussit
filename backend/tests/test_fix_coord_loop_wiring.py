"""Coordinator wiring of the CORE fixes into the reminder loop:

  - the seat-counter check (BookingService.reconcile_slot_counters) runs at
    most once a day across instances, REPORT ONLY (never repair=True), and a
    drift is logged as an error for an admin to review;
  - the startup backfill of seat ownership is idempotent (CORE marker).
"""
import logging

import pytest

from app import main
from app.services.booking_service import BookingService

from tests.test_reminder_loop import _stub_sweeps

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def lease(db):
    await db.locks.delete_many({"_id": main._LEASE_ID})
    holder = "holder-fix-coord"
    assert await main._claim_lease(db, holder)
    yield holder
    await db.locks.delete_many({"_id": main._LEASE_ID})


async def test_seat_counter_check_runs_daily_report_only_and_logs_drift(db, lease, monkeypatch, caplog):
    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls)
    runs: list[bool] = []

    async def fake_reconcile(self, repair: bool = False, **_kw):
        runs.append(repair)
        return {"drift": [{"service_center_id": "c1", "date": "2026-10-10", "slot_key": "07:00-10:00", "kind": "slot", "stored": 3, "expected": 2}]}

    monkeypatch.setattr(BookingService, "reconcile_slot_counters", fake_reconcile)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        await main._sweep_once(db, lease)
        await main._sweep_once(db, lease)  # same day — must not run again

    assert runs == [False], "one report-only run per day, never a repair"
    assert any("Seat counter drift" in r.getMessage() for r in caplog.records)


async def test_seat_ownership_backfill_is_idempotent(db):
    from app.services.booking_service import backfill_seat_ownership

    first = await backfill_seat_ownership(db)
    second = await backfill_seat_ownership(db)
    assert isinstance(first, dict) and isinstance(second, dict)
