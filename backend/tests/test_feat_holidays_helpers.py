"""Shared helpers for the test_feat_holidays_* suites (holiday system,
2026-10-19). No tests live here.

Holidays declared for ALL centers would refuse every other suite's
bookings, so each holiday test starts and ends with an empty `holidays`
collection AND a dropped in-process cache (`clean` fixture below)."""
import itertools
from datetime import date, datetime, timedelta

import pytest
from bson import ObjectId

from app.services.holiday_service import invalidate_holiday_cache
from tests import test_fix_core_helpers as h

_SPOT = itertools.count(1)


async def clear(db) -> None:
    await db.holidays.delete_many({})
    invalidate_holiday_cache()


@pytest.fixture
async def clean(db):
    created_centers: list[str] = []
    await clear(db)
    yield created_centers
    await clear(db)
    # Our centers stand far from everyone else's pins; switch them off so
    # no later nearest-center lookup or bot fallback ever lands on one.
    if created_centers:
        await db.service_centers.update_many(
            {"_id": {"$in": [ObjectId(c) for c in created_centers]}}, {"$set": {"is_active": False}}
        )


def day(offset: int) -> str:
    return h.day(offset)


def short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}"


def long(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


async def center(db, clean: list[str], **kw) -> tuple[str, str, float, float]:
    """A 09:00-19:00 center (slots 9-12, 12-3, 3-6, 6-7) on its own pincode,
    ~60 km from every other suite's pins. Returns (id, pincode, lat, lng)."""
    n = next(_SPOT)
    lat, lng = 23.30 + n * 0.02, 76.40
    cid, pin = await h.center(db, latitude=lat, longitude=lng, **kw)
    clean.append(cid)
    return cid, pin, lat, lng


async def admin_headers(db) -> dict:
    return (await h.admin(db))["h"]


def holiday_body(**kw) -> dict:
    body = {"message": "Diwali break — we'll be back soon!", "scope": "all", "start_date": day(1), "end_date": day(2)}
    body.update(kw)
    return {k: v for k, v in body.items() if v is not None}


async def make(client, headers: dict, **kw) -> dict:
    r = await client.post("/api/v1/admin/holidays", json=holiday_body(**kw), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def insert_raw(db, *, start: datetime, end: datetime, scope: str = "all", centers: list[str] | None = None,
                     message: str = "Raw holiday", notice_from: datetime | None = None) -> str:
    """A holiday written straight to the collection — for windows the API
    refuses on purpose (already started / in the past)."""
    from datetime import timezone

    now = datetime.now(timezone.utc)
    res = await db.holidays.insert_one({
        "message": message, "title": None, "scope": scope, "service_center_ids": centers or [],
        "start_at": start, "end_at": end, "notice_from": notice_from or start - timedelta(days=2),
        "notice_days_before": 2, "active": True, "created_by": "test", "created_at": now, "updated_at": now,
        "cancelled_at": None, "cancelled_by": None, "is_deleted": False,
    })
    invalidate_holiday_cache()
    return str(res.inserted_id)
