"""Shared helpers for the test_payroll_* suites (captain pay, §7.13). No tests
live here. Builds on the booking helpers: real HTTP, real services."""
import itertools
import random
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.finance_service import clear_cache
from tests import test_fix_core_helpers as h
from tests import test_feat_booking_helpers as fb

_KEY = itertools.count(1)
API = "/api/v1/payroll"


def key() -> str:
    return f"idem-{next(_KEY):06d}-{ObjectId()}"


async def rig(db, **center_kw) -> dict:
    """A center with its manager, a captain, a customer, and the admin."""
    s = await fb.rig(db, **center_kw)
    s["admin"] = await h.admin(db)
    s["db"] = db
    clear_cache()
    return s


async def second_center(db) -> dict:
    """Another center with its own manager and captain (isolation tests)."""
    cid, pin = await h.center(db)
    mgr = await h.manager(db, cid)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"manager_id": mgr["id"]}})
    return {"center_id": cid, "mgr": mgr, "cap": await h.captain(db, cid, wallet=500.0)}


async def set_pay(c, headers, captain_id: str, **body):
    return await c.put(f"{API}/captains/{captain_id}/terms", json=body, headers=headers)


async def make_payroll(c, s: dict, basis: str, *, as_admin: bool = False, **terms) -> dict:
    """Move s['cap'] onto salary / commission through the real terms route."""
    headers = s["admin"]["h"] if as_admin else s["mgr"]["h"]
    r = await set_pay(c, headers, s["cap"]["id"], basis=basis, **terms)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def complete_job(db, s: dict, when_slot: int = 0) -> dict:
    """A booking completed by the captain through the real after-photo path."""
    b = await fb.book(db, s["cu"], s["when"], s["keys"][when_slot])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "captain_id": s["cap"]["id"], "status": "service_started", "vehicle_verified": True,
        "service_started_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    }})
    await BookingService(db).capture_after_photo_and_complete(b["id"], await fb.hb.snap(db, s["cap"]["id"], "after"), s["cap"]["id"])
    return await fb.doc(db, b["id"])


async def ledger(db, captain_id: str, **flt) -> list[dict]:
    return await db.payroll_ledger.find({"captain_id": captain_id, **flt}).sort("_id", 1).to_list(None)


async def dues(db, captain_id: str) -> int:
    from app.repositories.payroll_repository import PayrollRepository

    return (await PayrollRepository(db).dues(captain_id))["dues_paise"]


async def wallet(db, captain_id: str) -> float:
    return float((await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"])


def phone() -> str:
    return "9" + f"{random.randint(0, 10**9 - 1):09d}"
