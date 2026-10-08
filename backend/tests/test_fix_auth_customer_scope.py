"""
AZ-01 + MGR-04 regression tests — a manager reads a customer's saved
addresses and plans only when that customer is known to THEIR center (the
same core/authz rule the 360 view uses): 404 otherwise, admins unrestricted,
customers never.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.security import create_access_token
from tests.factories import get_any_active_plan, get_hatchback_type_id, make_address, make_customer, make_manager
from tests.society_factories import make_admin, make_center

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


async def _customer_known_to(db, cleanup, center_id: str) -> str:
    cid = await make_customer(db)
    now = datetime.now(timezone.utc)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    cleanup.append(("addresses", {"owner_id": cid}))
    cleanup.append(("bookings", {"customer_id": cid}))
    cleanup.append(("user_subscriptions", {"customer_id": cid}))
    await make_address(db, cid)
    await db.bookings.insert_one({
        "booking_number": f"BK-FIXSCOPE-{cid[-6:]}", "customer_id": cid, "service_center_id": center_id, "status": "completed",
        "scheduled_date": now, "scheduled_slot": "09:00-12:00", "service_ids": [], "total_amount": 100,
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    await db.user_subscriptions.insert_one({
        "customer_id": cid, "plan_id": await get_any_active_plan(db), "status": "active", "vehicle_type": await get_hatchback_type_id(db),
        "start_date": now, "end_date": now + timedelta(days=30), "remaining_services": 2, "service_center_id": None,
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    return cid


async def test_manager_reads_only_own_center_customers(db, cleanup):
    A = await make_center(db, cleanup, 0)
    B = await make_center(db, cleanup, 1)
    mgr_a = await make_manager(db, A["id"])
    unlinked = await make_manager(db, None)
    admin = await make_admin(db, cleanup)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(mgr_a), ObjectId(unlinked)]}}))
    own = await _customer_known_to(db, cleanup, A["id"])
    other = await _customer_known_to(db, cleanup, B["id"])
    stranger = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(stranger)}))

    ha = _auth(mgr_a, "manager", A["id"])
    async with _client() as c:
        for path in ("/api/v1/addresses/customer/{}", "/api/v1/subscriptions/customer/{}"):
            # Manager A vs a center-B customer: 404 — not even confirmed to exist.
            r = await c.get(path.format(other), headers=ha)
            assert r.status_code == 404, (path, r.status_code, r.text)
            assert "line1" not in r.text
            # Manager A vs their own customer: allowed.
            r = await c.get(path.format(own), headers=ha)
            assert r.status_code == 200 and len(r.json()["data"]) == 1, (path, r.text)
            # A manager with no center gets nobody.
            assert (await c.get(path.format(own), headers=_auth(unlinked, "manager"))).status_code == 404
            # Admin: platform-wide.
            r = await c.get(path.format(other), headers=_auth(admin, "admin"))
            assert r.status_code == 200 and len(r.json()["data"]) == 1, (path, r.text)
            # A customer can't use the staff endpoint on anyone.
            assert (await c.get(path.format(other), headers=_auth(stranger, "customer"))).status_code == 403
            # Junk / staff ids are a plain 404 for the manager too.
            assert (await c.get(path.format("not-an-id"), headers=ha)).status_code == 404
            assert (await c.get(path.format(mgr_a), headers=ha)).status_code == 404


async def test_the_360_view_and_reset_use_the_same_rule(db, cleanup):
    from app.core.authz import customers_known_to_center

    A = await make_center(db, cleanup, 2)
    B = await make_center(db, cleanup, 3)
    own = await _customer_known_to(db, cleanup, A["id"])
    other = await _customer_known_to(db, cleanup, B["id"])
    assert await customers_known_to_center(db, [own, other], A["id"]) == {own}
    mgr_a = await make_manager(db, A["id"])
    cleanup.append(("users", {"_id": ObjectId(mgr_a)}))
    ha = _auth(mgr_a, "manager", A["id"])
    async with _client() as c:
        assert (await c.get(f"/api/v1/crm/customers/{other}", headers=ha)).status_code == 404
        assert (await c.get(f"/api/v1/crm/customers/{own}", headers=ha)).status_code == 200
        assert (await c.post(f"/api/v1/auth/customers/{other}/reset-password", headers=ha)).status_code == 404
