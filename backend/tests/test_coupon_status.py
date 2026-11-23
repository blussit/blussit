"""Coupon status — the admin list derives Active / Scheduled / Inactive /
Used Up / Expired on the server (never stored), filters by it, and the
customer-facing check refuses what the badge says is not live."""
import random
from datetime import timedelta

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import AppException
from app.services.coupon_service import CouponService, coupon_status
from app.utils.timezone import now_ist

pytestmark = pytest.mark.asyncio
API = "/api/v1/coupons"


def _auth(user_id: str, role: str, tv: int = 0) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': None, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60)


@pytest.fixture
async def admin(db):
    a = await db.users.find_one({"role": "admin"})
    return _auth(str(a["_id"]), "admin", a.get("token_version", 0))


async def _coupon(db, cleanup, *, until=30, start=-1, active=True, limit=None, used=0, tag="ST") -> str:
    now = now_ist()
    code = f"{tag}{random.randint(10**6, 10**7 - 1)}"
    res = await db.coupons.insert_one({
        "code": code, "coupon_type": "flat", "value": 50.0, "min_order_value": 0, "usage_limit_per_user": 5,
        "total_usage_limit": limit, "total_used": used, "valid_from": now + timedelta(days=start),
        "valid_until": now + timedelta(days=until), "offer_kind": "standard", "is_active": active, "is_deleted": False,
        "applies_to": ["services"], "stackable_with_offers": True, "combine_with_plan": False,
    })
    cleanup.append(("coupons", {"_id": res.inserted_id}))
    return code


async def _listed(client, admin, **params) -> dict[str, dict]:
    res = await client.get(API, params={"page_size": 100, **params}, headers=admin)
    assert res.status_code == 200, res.text
    return {c["code"]: c for c in res.json()["data"]}


async def test_status_is_derived_in_the_list(db, cleanup, admin):
    codes = {
        "active": await _coupon(db, cleanup),
        "expired": await _coupon(db, cleanup, until=-1, start=-10),
        # Switched on, but past its last day: still Expired.
        "expired_on": await _coupon(db, cleanup, until=-2, start=-10, active=True),
        "used_up": await _coupon(db, cleanup, limit=1, used=1),
        "inactive": await _coupon(db, cleanup, active=False),
        "scheduled": await _coupon(db, cleanup, start=3, until=10),
        # Expired wins over switched off and used up.
        "expired_off": await _coupon(db, cleanup, until=-1, start=-10, active=False, limit=1, used=1),
        "room_left": await _coupon(db, cleanup, limit=5, used=2),
    }
    async with _client() as c:
        rows = await _listed(c, admin)
    want = {
        "active": "active", "expired": "expired", "expired_on": "expired", "used_up": "used_up",
        "inactive": "inactive", "scheduled": "scheduled", "expired_off": "expired", "room_left": "active",
    }
    for name, status in want.items():
        assert rows[codes[name]]["status"] == status, name
    assert rows[codes["used_up"]]["status_label"] == "Used Up"
    assert rows[codes["expired"]]["status_label"] == "Expired"


async def test_status_filter(db, cleanup, admin):
    codes = {
        "active": await _coupon(db, cleanup),
        "expired": await _coupon(db, cleanup, until=-1, start=-10),
        "used_up": await _coupon(db, cleanup, limit=2, used=2),
        "inactive": await _coupon(db, cleanup, active=False),
        "scheduled": await _coupon(db, cleanup, start=3, until=10),
        "expired_off": await _coupon(db, cleanup, until=-1, start=-10, active=False),
    }
    async with _client() as c:
        for status in ("active", "expired", "used_up", "inactive", "scheduled"):
            rows = await _listed(c, admin, status=status)
            assert all(r["status"] == status for r in rows.values()), status
            mine = {n for n, code in codes.items() if code in rows}
            expect = {"active": {"active"}, "expired": {"expired", "expired_off"}, "used_up": {"used_up"},
                      "inactive": {"inactive"}, "scheduled": {"scheduled"}}[status]
            assert mine == expect, (status, mine)
        bad = await c.get(API, params={"status": "bogus"}, headers=admin)
        assert bad.status_code == 422


async def test_create_and_toggle_return_status(db, cleanup, admin):
    now = now_ist()
    code = f"NEW{random.randint(10**6, 10**7 - 1)}"
    body = {"code": code, "coupon_type": "flat", "value": 40, "valid_from": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=5)).isoformat()}
    async with _client() as c:
        res = await c.post(API, json=body, headers=admin)
        assert res.status_code == 200, res.text
        made = res.json()["data"]
        cleanup.append(("coupons", {"_id": ObjectId(made["id"])}))
        assert made["status"] == "active"
        off = await c.put(f"{API}/{made['id']}", json={"is_active": False}, headers=admin)
        assert off.json()["data"]["status"] == "inactive"
        past = await c.put(f"{API}/{made['id']}", json={"valid_from": (now - timedelta(days=9)).isoformat(),
                                                        "valid_until": (now - timedelta(days=2)).isoformat(), "is_active": True}, headers=admin)
        assert past.json()["data"]["status"] == "expired"


async def test_non_admin_cannot_list(db, cleanup):
    async with _client() as c:
        assert (await c.get(API)).status_code == 401
        cust = await db.users.find_one({"role": "customer"})
        if cust:
            assert (await c.get(API, headers=_auth(str(cust["_id"]), "customer"))).status_code == 403


async def test_customer_path_refuses_what_the_badge_calls_not_live(db, cleanup):
    svc = CouponService(db)
    expired = await _coupon(db, cleanup, until=-1, start=-10)
    used_up = await _coupon(db, cleanup, limit=1, used=1)
    off = await _coupon(db, cleanup, active=False)
    scheduled = await _coupon(db, cleanup, start=3, until=10)
    live = await _coupon(db, cleanup)
    for code in (expired, used_up, off, scheduled):
        with pytest.raises(AppException):
            await svc.validate_and_compute_discount(code, 500, None, use="services")
        with pytest.raises(AppException):
            await svc.validate_public_offer(code)
    coupon, discount = await svc.validate_and_compute_discount(live, 500, None, use="services")
    assert discount == 50
    assert (await svc.validate_public_offer(live))["code"] == live


async def test_status_is_ist_end_of_day():
    """valid_until is the end of the stated last day in IST (23:59:59+05:30,
    as the admin form sends it): still Active at 23:00 IST that day, Expired
    one minute into the next."""
    from datetime import datetime

    from app.utils.timezone import IST

    until = datetime(2026, 12, 1, 23, 59, 59, tzinfo=IST)
    doc = {"valid_from": datetime(2026, 11, 1, tzinfo=IST), "valid_until": until, "is_active": True}
    assert coupon_status(doc, datetime(2026, 12, 1, 23, 0, tzinfo=IST)) == "active"
    assert coupon_status(doc, datetime(2026, 12, 2, 0, 1, tzinfo=IST)) == "expired"
    assert coupon_status(doc, datetime(2026, 10, 31, 12, 0, tzinfo=IST)) == "scheduled"
