"""CAP-01 (payment side): a captain moved to another center, or switched
off, can no longer collect cash or show a QR for his old center's job —
the same rule every captain job step now applies (BookingService
._ensure_captain_job). Plus the recycle-bin purge skips bookings it may not
hard-delete (captain wallet money references them — ADM-11) instead of
retrying them every hour."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import ForbiddenException
from app.services.payment_service import PaymentService
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb

pytestmark = pytest.mark.asyncio


async def _completed_cash_job(db, s: dict) -> dict:
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "completed", "payment_status": "pending", "payment_method": "cash",
    }})
    return await hb.booking(db, b["id"])


async def test_moved_or_switched_off_captain_cannot_collect_for_old_center(db):
    s = await hb.staffed_center(db)
    job = await _completed_cash_job(db, s)
    jid, cap = str(job["_id"]), s["cap"]["id"]
    other_center, _pin = await h.center(db)
    payments = PaymentService(db)

    await db.users.update_one({"_id": ObjectId(cap)}, {"$set": {"service_center_id": other_center}})
    with pytest.raises(ForbiddenException):
        await payments.captain_collect_cash(jid, cap)
    assert (await hb.booking(db, jid))["payment_status"] == "pending"

    await db.users.update_one({"_id": ObjectId(cap)}, {"$set": {"service_center_id": s["center_id"], "status": "inactive"}})
    with pytest.raises(ForbiddenException):
        await payments.captain_collect_cash(jid, cap)

    await db.users.update_one({"_id": ObjectId(cap)}, {"$set": {"status": "active"}})
    done = await payments.captain_collect_cash(jid, cap)
    assert done is not None
    assert (await hb.booking(db, jid))["payment_status"] == "paid"


async def test_recycle_bin_purge_parks_bookings_it_may_not_delete(db, monkeypatch):
    from app import main
    from app.core.exceptions import BadRequestException
    from app.services.booking_service import BookingService

    s = await hb.staffed_center(db)
    b = await hb.new_booking(db, s["cu"], s["when"], s["keys"][1])
    old = datetime.now(timezone.utc) - timedelta(days=40)
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"is_deleted": True, "deleted_at": old}})
    calls: list[str] = []

    async def refuse(self, booking_id, force=False):
        calls.append(booking_id)
        raise BadRequestException("Captain wallet money references this booking")

    monkeypatch.setattr(BookingService, "permanently_delete_booking", refuse)
    await db.locks.delete_many({"_id": main._LEASE_ID})
    assert await main._claim_lease(db, "holder-purge")
    from tests.test_reminder_loop import _stub_sweeps

    _stub_sweeps(monkeypatch, [])
    await main._sweep_once(db, "holder-purge")
    await db.locks.update_many({"_id": main._LEASE_ID}, {"$unset": {"purge_sweep_at": ""}})
    await main._sweep_once(db, "holder-purge")
    await db.locks.delete_many({"_id": main._LEASE_ID})

    assert calls.count(b["id"]) == 1, "a refused booking is parked, not retried every pass"
    doc = await hb.booking(db, b["id"])
    assert doc.get("purge_blocked_reason")
