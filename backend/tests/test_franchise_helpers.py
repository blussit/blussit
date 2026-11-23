"""Shared helpers for test_franchise_* (franchise & service-center operation,
ARCHITECTURE.md §7.13 D18/D19). No tests."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services import finance_service
from app.utils.timezone import IST, now_ist
from tests import test_fix_core_helpers as h

BASE = "/api/v1/franchise"
CENTERS = "/api/v1/service-centers"
_N = itertools.count(1)


def today() -> str:
    return now_ist().date().isoformat()


def day(offset: int) -> str:
    return (now_ist().date() + timedelta(days=offset)).isoformat()


def partner(**kw) -> dict:
    return {"name": "Ravi Sharma", "phone": "9876543210", "email": "ravi@example.com", **kw}


def franchise_op(**kw) -> dict:
    op = {"type": "franchise", "partner": partner(), "revenue_share_percent": 70, "kits_purchased": 2}
    op.update(kw)
    return {k: v for k, v in op.items() if v is not Ellipsis}


def center_body(name=None, operation=None, **kw) -> dict:
    n = next(_N)
    b = {
        "name": name or f"FRX Center {n}",
        "location": {"address": "1 Test Road", "city": "Indore", "state": "MP", "pincode": f"45{n % 10000:04d}", "service_pincodes": []},
    }
    if operation is not None:
        b["operation"] = operation
    b.update(kw)
    return b


async def wipe(db) -> None:
    ids = [str(c["_id"]) async for c in db.service_centers.find({"name": {"$regex": "^FRX "}}, {"_id": 1})]
    if ids:
        await db.bookings.delete_many({"service_center_id": {"$in": ids}})
        await db.franchise_ledger.delete_many({"service_center_id": {"$in": ids}})
        await db.finance_entries.delete_many({"service_center_id": {"$in": ids}})
        await db.audit_logs.delete_many({"service_center_id": {"$in": ids}})
        await db.users.delete_many({"service_center_id": {"$in": ids}})
        await db.service_centers.delete_many({"_id": {"$in": [ObjectId(i) for i in ids]}})
    finance_service.clear_cache()


@pytest.fixture
async def clean(db):
    await wipe(db)
    yield
    await wipe(db)


async def admin_h(db) -> dict:
    return (await h.admin(db))["h"]


async def make_center(c, admin, operation=None, **kw) -> dict:
    r = await c.post(CENTERS, json=center_body(operation=operation, **kw), headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def put_center(c, admin, center_id, **body):
    return await c.put(f"{CENTERS}/{center_id}", json=body, headers=admin)


def completed_booking(center_id: str, total: float = 1000.0, *, when: datetime | None = None, **kw) -> dict:
    """A completed, paid booking row (rupee floats, as stored)."""
    when = when or now_ist()
    n = next(_N)
    doc = {
        "booking_number": f"FRX{ObjectId()}",
        "service_center_id": center_id,
        "status": "completed",
        "payment_status": "paid",
        "payment_method": "cash",
        "subtotal": total,
        "discount_amount": 0.0,
        "total_amount": total,
        "amount_paid": total,
        "paid_cash": total,
        "paid_online": 0.0,
        "captain_id": f"cap{n}",
        "scheduled_date": when.astimezone(IST).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0),
        "completed_at": when,
        "is_deleted": False,
    }
    doc.update(kw)
    return doc


async def add_booking(db, center_id: str, total: float = 1000.0, **kw) -> str:
    return str((await db.bookings.insert_one(completed_booking(center_id, total, **kw))).inserted_id)


async def sync(db, booking_id: str, session=None):
    from app.services.franchise_service import FranchiseService

    await FranchiseService(db).sync_share({"_id": ObjectId(booking_id)}, session=session)


async def booking(db, booking_id: str) -> dict:
    return await db.bookings.find_one({"_id": ObjectId(booking_id)})


async def ledger(db, center_id: str, **flt) -> list[dict]:
    return await db.franchise_ledger.find({"service_center_id": center_id, **flt}).sort("created_at", 1).to_list(length=None)


async def accrued(db, center_id: str) -> int:
    rows = await ledger(db, center_id, kind={"$in": ["share", "share_adjust"]})
    return sum(r["amount_paise"] for r in rows)
