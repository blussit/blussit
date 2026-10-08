"""
ADM-02, SOC-10, VAL-2 and ENUM-1 regression tests.

  - ADM-02: creating a staff account is audit-logged (actor, role, center)
    and refuses a center that doesn't exist or is switched off; the new
    account's phone starts unverified.
  - SOC-10: a car on a live (active/paused) car-bound pass can't be deleted.
  - VAL-2: only ASCII digits make a phone number — no look-alike duplicates.
  - ENUM-1: an unknown login identifier costs the same bcrypt check as a
    real one.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.security import create_access_token
from app.utils.phone import validate_indian_mobile
from tests.factories import get_any_active_plan, get_hatchback_type_id, make_customer, make_manager, make_vehicle
from tests.society_factories import make_admin, make_center

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


# ---------------------------------------------------------------- ADM-02
async def test_staff_creation_is_audited_and_center_validated(db, cleanup):
    A = await make_center(db, cleanup, 0)
    off = str((await db.service_centers.insert_one({"name": "Fix Off Ctr", "is_active": False, "is_deleted": False})).inserted_id)
    gone = str((await db.service_centers.insert_one({"name": "Fix Gone Ctr", "is_active": True, "is_deleted": True})).inserted_id)
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(off), ObjectId(gone)]}}))
    admin = await make_admin(db, cleanup)
    manager = await make_manager(db, A["id"])
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    cleanup.append(("users", {"phone": {"$in": ["9766600001", "9766600002", "9766600003", "9766600004"]}}))
    base = {"full_name": "New Staff", "password": "Temp@123456", "role": "manager"}
    async with _client() as c:
        ah = _auth(admin, "admin")
        for center in ("5f0000000000000000000000", "not-an-id", off, gone):
            r = await c.post("/api/v1/auth/staff", headers=ah, json={**base, "phone": "9766600001", "service_center_id": center})
            assert r.status_code == 400, (center, r.text)
        assert await db.users.count_documents({"phone": "9766600001"}) == 0

        r = await c.post("/api/v1/auth/staff", headers=ah, json={**base, "phone": "9766600002", "service_center_id": A["id"]})
        assert r.status_code == 200, r.text
        new_id = r.json()["data"]["id"]
        stored = await db.users.find_one({"_id": ObjectId(new_id)})
        assert stored["phone_verified"] is False
        audit = await db.audit_logs.find_one({"action": "CREATE_STAFF", "target_id": new_id})
        assert audit and audit["actor_id"] == admin and audit["actor_role"] == "admin"
        assert audit["details"]["role"] == "manager" and audit["details"]["service_center_id"] == A["id"]

        r = await c.post("/api/v1/auth/staff", headers=ah, json={**base, "role": "admin", "phone": "9766600003"})
        assert r.status_code == 200, r.text
        audit = await db.audit_logs.find_one({"action": "CREATE_STAFF", "target_id": r.json()["data"]["id"]})
        assert audit and audit["details"]["role"] == "admin"

        # A manager creates captains in their own center only — audited too.
        r = await c.post("/api/v1/auth/staff", headers=_auth(manager, "manager", A["id"]),
                         json={**base, "role": "captain", "phone": "9766600004", "service_center_id": off})
        assert r.status_code == 200, r.text
        cap = await db.users.find_one({"_id": ObjectId(r.json()["data"]["id"])})
        assert cap["service_center_id"] == A["id"]
        audit = await db.audit_logs.find_one({"action": "CREATE_STAFF", "target_id": str(cap["_id"])})
        assert audit and audit["actor_id"] == manager and audit["details"]["service_center_id"] == A["id"]
    await db.audit_logs.delete_many({"action": "CREATE_STAFF", "actor_id": {"$in": [admin, manager]}})


async def test_unlinked_manager_cannot_create_captains(db, cleanup):
    manager = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    async with _client() as c:
        r = await c.post("/api/v1/auth/staff", headers=_auth(manager, "manager"),
                         json={"full_name": "Orphan Cap", "password": "Temp@123456", "role": "captain", "phone": "9766600011"})
    assert r.status_code == 403, r.text
    assert await db.users.count_documents({"phone": "9766600011"}) == 0


# ---------------------------------------------------------------- SOC-10
@pytest.mark.parametrize("status,ends_in_days,blocked", [
    ("active", 10, True), ("paused", 10, True), ("active", -1, False), ("cancelled", 10, False), ("expired", -5, False),
])
async def test_car_on_a_live_pass_cant_be_deleted(db, cleanup, status, ends_in_days, blocked):
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    cleanup.append(("vehicles", {"owner_id": cid}))
    cleanup.append(("user_subscriptions", {"customer_id": cid}))
    hatch = await get_hatchback_type_id(db)
    vid = await make_vehicle(db, cid, hatch)
    now = datetime.now(timezone.utc)
    await db.user_subscriptions.insert_one({
        "customer_id": cid, "plan_id": await get_any_active_plan(db), "status": status, "vehicle_id": vid, "vehicle_type": hatch,
        "society_id": "soc-fix", "start_date": now - timedelta(days=5), "end_date": now + timedelta(days=ends_in_days),
        "remaining_services": 2, "is_deleted": False, "created_at": now, "updated_at": now,
    })
    async with _client() as c:
        r = await c.delete(f"/api/v1/vehicles/{vid}", headers=_auth(cid, "customer"))
    if blocked:
        assert r.status_code == 400 and "plan" in r.json()["message"], r.text
        assert (await db.vehicles.find_one({"_id": ObjectId(vid)})).get("is_deleted") is not True
    else:
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------- VAL-2
@pytest.mark.parametrize("raw", ["9٨76543210", "٩٨٧٦٥٤٣٢١٠", "９８７６５４３２１０", "+91 ９８７６５４３２１０", "98765४3210", "98765 4321O", "phone 9876543210"])
async def test_non_ascii_or_non_digit_phone_is_invalid(raw):
    assert validate_indian_mobile(raw) is None


@pytest.mark.parametrize("raw", ["9876543210", "+91 98765 43210", "+91-98765-43210", "09876543210", "919876543210", "(+91) 98765.43210", "98765 43210"])
async def test_normal_phone_formats_still_work(raw):
    assert validate_indian_mobile(raw) == "9876543210"


async def test_lookalike_phone_cannot_create_a_duplicate_customer(db, cleanup):
    A = await make_center(db, cleanup, 1)
    manager = await make_manager(db, A["id"])
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    async with _client() as c:
        r = await c.post("/api/v1/auth/customers", headers=_auth(manager, "manager", A["id"]),
                         json={"full_name": "Lookalike", "phone": "9٨76543210", "temp_password": "Temp@12345"})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------- ENUM-1
async def test_unknown_identifier_still_pays_for_a_bcrypt_check(db, cleanup, monkeypatch):
    from app.core import security

    calls = []
    real = security.pwd_context.verify

    def counting(secret, hashed, *a, **k):
        calls.append(hashed)
        return real(secret, hashed, *a, **k)

    monkeypatch.setattr(security.pwd_context, "verify", counting)
    pwless = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(pwless)}))
    await db.users.update_one({"_id": ObjectId(pwless)}, {"$set": {"password_hash": None}})
    phone = (await db.users.find_one({"_id": ObjectId(pwless)}))["phone"]
    async with _client() as c:
        for ident in ("9766699999", "nobody.fix@example.com", phone):
            calls.clear()
            r = await c.post("/api/v1/auth/login", json={"identifier": ident, "password": "Whatever@1"})
            assert r.status_code == 401 and r.json()["message"] == "Invalid credentials"
            assert len(calls) == 1, ident
