"""
CAP-06 + FE-13 regression tests — account standing.

User statuses: "active" (normal), "pending" (onboarding — may sign in, e.g.
a new captain finishing KYC), "inactive" and "suspended" (switched off).
An inactive or suspended account is refused at login, on every use of an
existing access token, and on refresh — for every role. A switched-off
customer reached through a by-phone booking path gets a 400 with a clear
message (it used to be a 401, which the app reads as "session expired").
"""
from datetime import datetime, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.core.security import hash_password
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient
_n = iter(range(1, 999))


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


async def _user(db, cleanup, role: str) -> dict:
    i = next(_n)
    phone = f"97555{i:05d}"
    now = datetime.now(timezone.utc)
    doc = {
        "full_name": f"Standing {role} {i}", "email": f"fix.standing.{role}.{i}@example.com", "phone": phone,
        "password_hash": hash_password("Stand@12345"), "role": role, "status": "active", "is_deleted": False,
        "phone_verified": True, "phone_verified_at": now, "created_at": now, "updated_at": now,
    }
    uid = str((await db.users.insert_one(doc)).inserted_id)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    return {"id": uid, **doc}


@pytest.mark.parametrize("role", ["customer", "captain", "manager", "admin"])
@pytest.mark.parametrize("status", ["inactive", "suspended"])
async def test_switched_off_account_is_refused_everywhere(db, cleanup, role, status):
    user = await _user(db, cleanup, role)
    async with _client() as c:
        tokens = (await c.post("/api/v1/auth/login", json={"identifier": user["email"], "password": "Stand@12345"})).json()["data"]
        await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"status": status}})
        login = await c.post("/api/v1/auth/login", json={"identifier": user["email"], "password": "Stand@12345"})
        me = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        refresh = await c.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert login.status_code == 401, login.text
    assert me.status_code == 401, me.text
    assert refresh.status_code == 401, refresh.text
    assert status in login.json()["message"]


async def test_pending_account_can_still_sign_in(db, cleanup):
    user = await _user(db, cleanup, "captain")
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"status": "pending"}})
    async with _client() as c:
        login = await c.post("/api/v1/auth/login", json={"identifier": user["email"], "password": "Stand@12345"})
        assert login.status_code == 200, login.text
        me = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"})
    assert me.status_code == 200


@pytest.mark.parametrize("status", ["inactive", "suspended"])
async def test_switched_off_customer_otp_paths_are_refused(db, cleanup, status):
    user = await _user(db, cleanup, "customer")
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"status": status}})
    cleanup.append(("otp_requests", {"identifier": user["phone"]}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{user['phone']}"}))
    async with _client() as c:
        r = await c.post("/api/v1/auth/otp/request", json={"identifier": user["phone"]})
        assert r.status_code in (401, 403) and status in r.json()["message"], r.text
        assert await db.otp_requests.find_one({"identifier": user["phone"]}) is None


@pytest.mark.parametrize("status", ["inactive", "suspended"])
async def test_by_phone_booking_paths_answer_400_not_401(db, cleanup, status):
    user = await _user(db, cleanup, "customer")
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"status": status}})
    with pytest.raises(BadRequestException) as exc:
        await AuthService(db).ensure_customer_by_phone(user["phone"], "Anyone")
    assert exc.value.status_code == 400 and exc.value.error_code == "ACCOUNT_INACTIVE"
    assert "support" in exc.value.message.lower()

    # Over HTTP: a manager adding this number as a society resident.
    from app.core.security import create_access_token
    from tests.factories import make_manager
    from tests.society_factories import make_center, make_society, make_template

    center = await make_center(db, cleanup, 4)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    manager = await make_manager(db, center["id"])
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    h = {"Authorization": f"Bearer {create_access_token(manager, 'manager', {'service_center_id': center['id'], 'tv': 0})}"}
    async with _client() as c:
        r = await c.post(f"/api/v1/societies/{society['id']}/enrollments", headers=h, json={
            "phone": user["phone"], "resident_name": "Anyone", "flat": "A-101", "plan_id": plan["id"],
            "cars": [{"vehicle_type": (await db.vehicle_types.find_one({"slug": "hatchback"}))["_id"].__str__(), "registration_number": "MP04FX0001"}],
        })
    assert r.status_code == 400 and r.json()["error_code"] == "ACCOUNT_INACTIVE", r.text


async def test_last_admin_cant_be_switched_off_and_inactive_admins_dont_count(db, cleanup):
    from app.core.security import create_access_token

    others = await db.users.find({"role": "admin", "status": {"$nin": ["suspended", "inactive"]}, "is_deleted": {"$ne": True}}).to_list(None)
    a = await _user(db, cleanup, "admin")
    b = await _user(db, cleanup, "admin")
    # Only a and b may be live admins for this check.
    await db.users.update_many({"_id": {"$in": [o["_id"] for o in others]}}, {"$set": {"status": "inactive"}})
    try:
        ha = {"Authorization": f"Bearer {create_access_token(a['id'], 'admin', {'service_center_id': None, 'tv': 0})}"}
        async with _client() as c:
            assert (await c.put(f"/api/v1/users/{b['id']}", headers=ha, json={"status": "inactive"})).status_code == 200
            # b is inactive now, so a is the only admin who can sign in —
            # the last-admin guard must not count b as "another admin".
            from app.services.user_service import UserService
            from app.core.exceptions import BadRequestException

            with pytest.raises(BadRequestException, match="only active admin"):
                await UserService(db).ensure_not_last_admin(a["id"])
    finally:
        await db.users.update_many({"_id": {"$in": [o["_id"] for o in others]}}, {"$set": {"status": "active"}})
