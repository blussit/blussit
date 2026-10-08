"""
OPS remediation (audit 2026-10-07) — misc.

CAP-02 (OPS part)  every photo upload is recorded (uploaded_photos, _id =
                   storage key, uploader, role, time) and can be CLAIMED
                   once, atomically, by the captain who uploaded it — so a
                   URL can't be reused as both before and after photo, or
                   across bookings, or by another captain. (CORE wires the
                   claim into the captain photo steps.)
ADM-14             plan_washes_count counted legacy rows with no
                   subscription_id field (fix lives in payment_service.py —
                   not OPS-owned; strict xfail until it lands).
Founder decision   manager discounts and tips count in revenue and must be
                   visible to the admin: KPI overview / manager overview /
                   financial carry them as their own lines.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from tests.factories import make_captain, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------- CAP-02


@pytest.fixture
async def crew(db, cleanup, tmp_path, monkeypatch):
    from app.core import storage

    monkeypatch.setattr(storage.settings, "STORAGE_PROVIDER", "local")
    monkeypatch.setattr(storage.settings, "UPLOAD_DIR", str(tmp_path))
    center = await make_service_center(db)
    cap = await make_captain(db, center)
    other = await make_captain(db, center)
    for uid in (cap, other):
        cleanup.append(("captain_wallets", {"captain_id": uid}))
        cleanup.append(("uploaded_photos", {"uploader_id": uid}))
        cleanup.append(("upload_counters", {"_id": {"$regex": f"^{uid}:"}}))
    cleanup.append(("users", {"_id": {"$in": [ObjectId(cap), ObjectId(other)]}}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    return {"center": center, "cap": cap, "other": other}


async def _upload(c, crew, who="cap") -> str:
    res = await c.post(
        "/api/v1/uploads/photo", headers=_auth(crew[who], "captain", crew["center"]),
        files={"file": ("x.jpg", _JPEG, "image/jpeg")},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["url"]


async def test_every_photo_upload_is_recorded(db, crew):
    from app.core.storage import photo_key_for_url

    async with _client() as c:
        url = await _upload(c, crew)
    key = photo_key_for_url(url)
    assert key and key.startswith("photos/")
    rec = await db.uploaded_photos.find_one({"_id": key})
    assert rec is not None
    assert rec["url"] == url and rec["uploader_id"] == crew["cap"] and rec["role"] == "captain"
    assert rec["service_center_id"] == crew["center"]
    assert isinstance(rec["created_at"], datetime) and rec.get("used_by") is None


async def test_a_photo_can_be_claimed_once_only_by_its_uploader(db, crew):
    from app.core.storage import claim_uploaded_photo

    async with _client() as c:
        url = await _upload(c, crew)
        other_url = await _upload(c, crew, "other")
    # Someone else's upload, or a URL we never handed out: refused.
    assert await claim_uploaded_photo(db, other_url, crew["cap"], booking_id="b1", step="before") is None
    assert await claim_uploaded_photo(db, "https://evil.example/x.jpg", crew["cap"], booking_id="b1", step="before") is None
    first = await claim_uploaded_photo(db, url, crew["cap"], booking_id="b1", step="before")
    assert first is not None and first["used_by"]["booking_id"] == "b1" and first["used_by"]["step"] == "before"
    # The same file as the after photo, or on another booking: refused.
    assert await claim_uploaded_photo(db, url, crew["cap"], booking_id="b1", step="after") is None
    assert await claim_uploaded_photo(db, url, crew["cap"], booking_id="b2", step="before") is None


async def test_an_old_upload_cannot_be_claimed(db, crew):
    from app.core.storage import claim_uploaded_photo, photo_key_for_url

    async with _client() as c:
        url = await _upload(c, crew)
    await db.uploaded_photos.update_one(
        {"_id": photo_key_for_url(url)}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(days=2)}},
    )
    assert await claim_uploaded_photo(db, url, crew["cap"], booking_id="b1", step="before") is None
    assert await claim_uploaded_photo(db, url, crew["cap"], booking_id="b1", step="before", max_age=timedelta(days=3)) is not None


def test_photo_key_for_r2_urls(monkeypatch):
    from app.core import storage

    monkeypatch.setattr(storage.settings, "R2_PUBLIC_BASE_URL", "https://cdn.blussit.test")
    assert storage.photo_key_for_url("https://cdn.blussit.test/photos/abc123.jpg") == "photos/abc123.jpg"
    assert storage.photo_key_for_url("https://cdn.blussit.test/../etc/passwd") is None
    assert storage.photo_key_for_url("https://elsewhere.test/photos/abc123.jpg") is None


# ---------------------------------------------------------------- ADM-14


async def test_plan_washes_count_ignores_rows_without_a_subscription_field(db, cleanup):
    from app.services.payment_service import PaymentService

    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("bookings", {"service_center_id": center}))
    day = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    base = {"service_center_id": center, "status": "completed", "payment_status": "paid", "payment_method": "cash",
            "total_amount": 100, "scheduled_date": day, "is_deleted": False, "completed_by_role": "manager"}
    await db.bookings.insert_many([
        {**base, "booking_number": f"BK14A{ObjectId()}"},  # legacy row: no subscription_id field at all
        {**base, "booking_number": f"BK14B{ObjectId()}", "subscription_id": None},
        {**base, "booking_number": f"BK14C{ObjectId()}", "subscription_id": "000000000000000000000001", "payment_method": "subscription", "total_amount": 0},
    ])
    d = day.date().isoformat()
    out = await PaymentService(db).center_collections(center, "admin", None, d, d)
    assert out["totals"]["washes_count"] == 3
    assert out["totals"]["plan_washes_count"] == 1


# ---------------------------------------------------------------- tips / manager discounts


async def test_kpis_show_manager_discounts_and_tips_as_their_own_lines(db, cleanup):
    from app.services.kpi_service import KpiService, resolve_period

    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("users", {"_id": ObjectId(mgr)}))
    cleanup.append(("bookings", {"service_center_id": center}))
    now = datetime.now(timezone.utc)
    base = {"service_center_id": center, "status": "completed", "payment_status": "paid", "payment_method": "cash",
            "created_at": now, "closed_at": now, "is_deleted": False, "completed_by_role": "manager", "captain_earning": 0.0}
    await db.bookings.insert_many([
        {**base, "booking_number": f"BKTIP{ObjectId()}", "subtotal": 349, "manager_discount": 49, "discount_amount": 49, "tip_amount": 30, "total_amount": 330},
        {**base, "booking_number": f"BKTIP{ObjectId()}", "subtotal": 499, "total_amount": 499},
        # Not completed: never counted.
        {**base, "booking_number": f"BKTIP{ObjectId()}", "status": "pending", "manager_discount": 100, "tip_amount": 100, "total_amount": 0},
    ])
    s, e, ps, pe = resolve_period("today", None, None)
    kpi = KpiService(db)
    mine = await kpi.manager_overview(center, s, e, ps, pe)
    assert mine["current"]["manager_discounts"] == 49
    assert mine["current"]["tips"] == 30
    overview = await kpi.overview(s, e, ps, pe)
    assert overview["current"]["manager_discounts"] >= 49 and overview["current"]["tips"] >= 30
    fin = await kpi.financial(s, e, ps, pe)
    assert fin["manager_discounts"] >= 49 and fin["tips_included"] >= 30
