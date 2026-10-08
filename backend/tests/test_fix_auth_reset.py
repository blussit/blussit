"""
AUTH-01 (P0-2) + AUTH-04 regression tests — self-service password reset.

  - staff (admin / manager / captain) can reset a password by code ONLY
    after proving their phone themselves while signed in (verify-phone
    request + confirm); an unverified, placeholder or merely-flagged phone
    is refused with "ask your admin" and NO code is sent;
  - the MSG91 widget reset applies the same rule;
  - a code is bound to its purpose: a booking-confirmation or login code
    never resets a password, a reset code never logs in;
  - wrong code / replay / two parallel resets with one code: only one wins;
  - a customer's reset keeps working;
  - the admin/manager temporary-password reset is the staff recovery path.
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.services.msg91_widget_service import Msg91WidgetService
from tests.factories import make_customer

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient
_PHONES = iter(range(9733300001, 9733300999))


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _track(cleanup, phone: str):
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("used_widget_tokens", {}))


async def _staff(db, cleanup, role: str, *, phone: str | None = None, verified: bool = False, center: str | None = None) -> dict:
    phone = phone or str(next(_PHONES))
    now = datetime.now(timezone.utc)
    doc = {
        "full_name": f"Fix {role}", "email": f"fix.{role}.{phone}@example.com", "phone": phone,
        "password_hash": hash_password("Staff@12345"), "role": role, "status": "active", "service_center_id": center,
        "is_deleted": False, "created_at": now, "updated_at": now,
        # "verified" here = the flag alone (legacy / bot-set) — NOT the
        # signed-in self-verification the reset now requires.
        "phone_verified": verified, "phone_verified_at": now if verified else None,
    }
    uid = str((await db.users.insert_one(doc)).inserted_id)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    _track(cleanup, phone)
    return {"id": uid, "phone": phone, "email": doc["email"], "role": role, "center": center}


async def _code(db, phone: str) -> str | None:
    rec = await db.otp_requests.find_one({"identifier": phone, "otp": {"$exists": True}})
    return rec["otp"] if rec else None


async def _uncool(db, phone: str):
    await db.otp_requests.update_many({"identifier": phone}, {"$set": {"last_sent_at": datetime.now(timezone.utc) - timedelta(seconds=60)}})


async def _self_verify(c, db, staff: dict):
    """The authenticated "verify my phone" flow, as the staff member."""
    h = _auth(staff["id"], staff["role"], staff["center"])
    r = await c.post("/api/v1/auth/verify-phone/request", headers=h)
    assert r.status_code == 200, r.text
    r = await c.post("/api/v1/auth/verify-phone/confirm", headers=h, json={"otp": await _code(db, staff["phone"])})
    assert r.status_code == 200, r.text
    await _uncool(db, staff["phone"])


def _fake_jwt(payload: dict) -> str:
    def part(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(payload)}.sig"


def _stub_msg91(monkeypatch, phone: str):
    class R:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"type": "success", "message": f"91{phone}"}

    async def post(self, token):
        return R()

    monkeypatch.setattr(Msg91WidgetService, "_post_verify", post)
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", "test-auth-key")


async def _login(c, identifier: str, password: str) -> int:
    return (await c.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})).status_code


@pytest.mark.parametrize("role", ["admin", "manager", "captain"])
@pytest.mark.parametrize("flag_only", [False, True])
async def test_unverified_staff_cannot_reset_by_code_and_no_code_is_sent(db, cleanup, monkeypatch, role, flag_only):
    staff = await _staff(db, cleanup, role, verified=flag_only)
    _stub_msg91(monkeypatch, staff["phone"])
    async with _client() as c:
        for ident in (staff["phone"], staff["email"]):
            r = await c.post("/api/v1/auth/forgot-password", json={"identifier": ident})
            assert r.status_code == 400, r.text
            assert "staff account" in r.json()["message"] and "admin" in r.json()["message"]
        assert await db.whatsapp_outbox.count_documents({"phone": staff["phone"]}) == 0
        assert await _code(db, staff["phone"]) is None

        # A code that reached the phone some other way (the public booking
        # popup) can't do it either, nor can an MSG91 widget token.
        assert (await c.post("/api/v1/bookings/verify-phone/request", json={"phone": staff["phone"]})).status_code == 200
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": staff["phone"], "otp": await _code(db, staff["phone"]), "new_password": "Owned@123456"})
        assert r.status_code == 400 and "admin" in r.json()["message"], r.text
        r = await c.post("/api/v1/auth/reset-password/widget", json={"access_token": _fake_jwt({"identifier": f"91{staff['phone']}"}), "phone": staff["phone"], "new_password": "Owned@123456"})
        assert r.status_code == 400 and "admin" in r.json()["message"], r.text

        assert await _login(c, staff["email"], "Owned@123456") == 401
        assert await _login(c, staff["email"], "Staff@12345") == 200


async def test_seed_placeholder_staff_cannot_be_taken_over(db):
    """P0-2's exact reproduction: forgot-password on the seed admin, whose
    phone is the placeholder 9999999999 — refused, nothing sent."""
    async with _client() as c:
        for email in ("admin@doorstepvehiclecare.in", "manager.indore@doorstepvehiclecare.in", "captain.indore@doorstepvehiclecare.in"):
            user = await db.users.find_one({"email": email})
            assert user, email
            before = await db.whatsapp_outbox.count_documents({"phone": user["phone"]})
            for ident in (email, user["phone"]):
                r = await c.post("/api/v1/auth/forgot-password", json={"identifier": ident})
                assert r.status_code == 400 and "admin" in r.json()["message"], r.text
            assert await db.whatsapp_outbox.count_documents({"phone": user["phone"]}) == before
            assert await db.otp_requests.find_one({"identifier": user["phone"], "purpose": "password_reset"}) is None


@pytest.mark.parametrize("role", ["admin", "manager", "captain"])
async def test_self_verified_staff_can_reset_and_code_is_single_use(db, cleanup, role):
    staff = await _staff(db, cleanup, role)
    async with _client() as c:
        await _self_verify(c, db, staff)
        stored = await db.users.find_one({"_id": ObjectId(staff["id"])})
        assert stored["phone_verified"] is True and stored["self_verified_phone"] == staff["phone"]
        me = (await c.get("/api/v1/auth/me", headers=_auth(staff["id"], role))).json()["data"]
        assert me["phone_self_verified"] is True

        r = await c.post("/api/v1/auth/forgot-password", json={"identifier": staff["email"]})
        assert r.status_code == 200, r.text
        code = await _code(db, staff["phone"])
        wrong = "000000" if code != "000000" else "111111"
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": staff["phone"], "otp": wrong, "new_password": "Mine@123456"})
        assert r.status_code == 400
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": staff["phone"], "otp": code, "new_password": "Mine@123456"})
        assert r.status_code == 200, r.text
        replay = await c.post("/api/v1/auth/reset-password", json={"identifier": staff["phone"], "otp": code, "new_password": "Other@123456"})
        assert replay.status_code == 400
        assert await _login(c, staff["email"], "Mine@123456") == 200
        assert await _login(c, staff["email"], "Other@123456") == 401


async def test_admin_phone_change_drops_the_self_verification(db, cleanup):
    staff = await _staff(db, cleanup, "manager")
    admin = await _staff(db, cleanup, "admin")
    async with _client() as c:
        await _self_verify(c, db, staff)
        new_phone = str(next(_PHONES))
        _track(cleanup, new_phone)
        r = await c.put(f"/api/v1/users/{staff['id']}", headers=_auth(admin["id"], "admin"), json={"phone": new_phone})
        assert r.status_code == 200, r.text
        stored = await db.users.find_one({"_id": ObjectId(staff["id"])})
        assert stored["phone"] == new_phone and stored["phone_verified"] is False
        r = await c.post("/api/v1/auth/forgot-password", json={"identifier": new_phone})
        assert r.status_code == 400 and "admin" in r.json()["message"]
        assert await db.whatsapp_outbox.count_documents({"phone": new_phone}) == 0


async def test_two_parallel_resets_with_one_code_only_one_wins(db, cleanup):
    staff = await _staff(db, cleanup, "captain")
    async with _client() as c:
        await _self_verify(c, db, staff)
        assert (await c.post("/api/v1/auth/forgot-password", json={"identifier": staff["phone"]})).status_code == 200
        code = await _code(db, staff["phone"])
        results = await asyncio.gather(*[
            c.post("/api/v1/auth/reset-password", json={"identifier": staff["phone"], "otp": code, "new_password": f"Race@{i}23456"})
            for i in range(6)
        ])
    assert sorted(r.status_code for r in results) == [200] + [400] * 5


async def test_customer_reset_still_works(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    _track(cleanup, user["phone"])
    async with _client() as c:
        assert (await c.post("/api/v1/auth/forgot-password", json={"identifier": user["phone"]})).status_code == 200
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": user["phone"], "otp": await _code(db, user["phone"]), "new_password": "Cust@123456"})
        assert r.status_code == 200, r.text
        assert await _login(c, user["phone"], "Cust@123456") == 200


async def test_code_purpose_is_enforced(db, cleanup):
    """AUTH-04: the public booking popup's code (and a login code) can't
    reset a password; a reset code can't log in; a refused purpose doesn't
    spend the code or an attempt."""
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    _track(cleanup, phone)
    from app.services.auth_service import AuthService

    async with _client() as c:
        assert (await c.post("/api/v1/bookings/verify-phone/request", json={"phone": phone})).status_code == 200
        booking_code = await _code(db, phone)
        assert (await db.otp_requests.find_one({"identifier": phone}))["purpose"] == "booking_confirmation"
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": phone, "otp": booking_code, "new_password": "Reset@123456"})
        assert r.status_code == 400, r.text
        assert (await db.otp_requests.find_one({"identifier": phone}))["attempts"] == 0
        assert await AuthService(db).verify_phone_proof(phone, booking_code, None) is True  # still good for its own job

        await _uncool(db, phone)
        assert (await c.post("/api/v1/auth/otp/request", json={"identifier": phone})).status_code == 200
        login_code = await _code(db, phone)
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": phone, "otp": login_code, "new_password": "Reset@123456"})
        assert r.status_code == 400
        assert (await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": login_code})).status_code == 200

        await _uncool(db, phone)
        assert (await c.post("/api/v1/auth/forgot-password", json={"identifier": phone})).status_code == 200
        reset_code = await _code(db, phone)
        assert (await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": reset_code})).status_code == 400
        assert await AuthService(db).verify_phone_proof(phone, reset_code, None) is False
        r = await c.post("/api/v1/auth/reset-password", json={"identifier": phone, "otp": reset_code, "new_password": "Reset@123456"})
        assert r.status_code == 200, r.text


async def test_staff_temporary_password_reset_is_the_recovery_path(db, cleanup):
    center = str((await db.service_centers.insert_one({"name": "Fix Reset Ctr", "is_active": True, "is_deleted": False})).inserted_id)
    other = str((await db.service_centers.insert_one({"name": "Fix Reset Ctr B", "is_active": True, "is_deleted": False})).inserted_id)
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    admin = await _staff(db, cleanup, "admin")
    manager = await _staff(db, cleanup, "manager", center=center)
    captain = await _staff(db, cleanup, "captain", center=center)
    captain_b = await _staff(db, cleanup, "captain", center=other)
    other_manager = await _staff(db, cleanup, "manager", center=other)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    async with _client() as c:
        mh = _auth(manager["id"], "manager", center)
        body = {"temp_password": "Temp@123456"}
        # Manager: own center's captains only; never managers/admins/customers.
        assert (await c.post(f"/api/v1/auth/staff/{captain_b['id']}/reset-password", headers=mh, json=body)).status_code == 404
        assert (await c.post(f"/api/v1/auth/staff/{other_manager['id']}/reset-password", headers=mh, json=body)).status_code == 404
        assert (await c.post(f"/api/v1/auth/staff/{admin['id']}/reset-password", headers=mh, json=body)).status_code == 404
        assert (await c.post(f"/api/v1/auth/staff/{customer_id}/reset-password", headers=mh, json=body)).status_code == 404
        assert (await c.post(f"/api/v1/auth/staff/{captain['id']}/reset-password", headers=_auth(customer_id, "customer"), json=body)).status_code == 403
        r = await c.post(f"/api/v1/auth/staff/{captain['id']}/reset-password", headers=mh, json=body)
        assert r.status_code == 200, r.text
        assert "Temp@123456" not in r.text
        stored = await db.users.find_one({"_id": ObjectId(captain["id"])})
        assert stored["must_change_password"] is True and stored.get("token_version") == 1
        assert await _login(c, captain["email"], "Temp@123456") == 200
        # Admin: any staff account but their own.
        ah = _auth(admin["id"], "admin")
        assert (await c.post(f"/api/v1/auth/staff/{other_manager['id']}/reset-password", headers=ah, json=body)).status_code == 200
        assert (await c.post(f"/api/v1/auth/staff/{admin['id']}/reset-password", headers=ah, json=body)).status_code == 400
        audit = await db.audit_logs.find_one({"action": "RESET_STAFF_PASSWORD", "target_id": captain["id"]})
        assert audit and audit["actor_id"] == manager["id"]


@pytest.mark.parametrize("deliverable", [False, True])
async def test_customer_temp_password_reset_never_locks_the_customer_out(db, cleanup, monkeypatch, deliverable):
    """DEP-04 follow-up: the manager's "reset customer password" checks the
    message can reach the customer BEFORE changing anything; if the send
    still fails, the old password is put back."""
    from app.services.whatsapp_service import WhatsAppService

    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    _track(cleanup, user["phone"])
    admin = await _staff(db, cleanup, "admin")

    async def can_deliver(self, phone):
        return deliverable

    async def send_fails(self, phone, pw):
        return False

    monkeypatch.setattr(WhatsAppService, "can_deliver_temp_password", can_deliver)
    monkeypatch.setattr(WhatsAppService, "send_temp_password", send_fails)
    async with _client() as c:
        r = await c.post(f"/api/v1/auth/customers/{customer_id}/reset-password", headers=_auth(admin["id"], "admin"))
        assert r.status_code == 400, r.text
        assert ("can't be sent" if not deliverable else "unchanged") in r.json()["message"]
        assert await _login(c, user["phone"], "Test@12345") == 200
    stored = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert stored["password_hash"] == user["password_hash"] and not stored.get("must_change_password")
