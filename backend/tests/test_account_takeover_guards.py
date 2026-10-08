"""
Account-identity guards (pre-production audit, 2026-10-06). Each test is an
attack that worked before the fix:
  - registering a stranger's phone with your own password, then keeping that
    password (and your sessions) after the real owner starts using the number;
  - "Continue with Google" joining an account someone else set a password on,
    or minting staff tokens;
  - a parallel burst of wrong passwords never tripping the 5-strike lock.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, UnauthorizedException
from app.core.security import hash_password
from app.schemas.user_schema import RegisterRequest
from app.services.auth_service import AuthService
from app.services.google_auth_service import GoogleAuthService

from tests.factories import make_manager

pytestmark = pytest.mark.asyncio


async def _squatted_account(db, cleanup, phone: str, password: str) -> str:
    """What an attacker's /auth/register used to leave behind: a password
    account on someone else's number, never proven."""
    now = datetime.now(timezone.utc)
    result = await db.users.insert_one({
        "full_name": "Squatter", "phone": phone, "password_hash": hash_password(password),
        "role": "customer", "status": "active", "is_deleted": False, "created_at": now, "updated_at": now,
    })
    cleanup.append(("users", {"_id": result.inserted_id}))
    cleanup.append(("otp_requests", {"identifier": phone}))
    return str(result.inserted_id)


async def _code(auth, db, phone):
    await auth.request_phone_otp(phone)
    return (await db.otp_requests.find_one({"identifier": phone}))["otp"]


async def test_register_refuses_a_phone_nobody_proved(db, cleanup):
    phone = "9811100001"
    cleanup.append(("otp_requests", {"identifier": phone}))
    auth = AuthService(db)
    with pytest.raises(BadRequestException):
        await auth.register_customer(RegisterRequest(full_name="Attacker", phone=phone, password="Attacker@123"))
    assert await db.users.count_documents({"phone": phone}) == 0

    code = await _code(auth, db, phone)
    created = await auth.register_customer(RegisterRequest(full_name="Owner", phone=phone, password="Owner@12345", phone_otp=code))
    cleanup.append(("users", {"_id": ObjectId(created["user"]["id"])}))
    assert (await db.users.find_one({"phone": phone}))["phone_verified"] is True


async def test_owner_proving_the_phone_evicts_a_planted_password_and_its_sessions(db, cleanup):
    phone = "9811100002"
    uid = await _squatted_account(db, cleanup, phone, "Attacker@123")
    auth = AuthService(db)
    attacker = await auth.login(phone, "Attacker@123")

    # The real owner signs in with an OTP — the account is theirs from now on.
    owner = await auth.otp_login(phone, otp=await _code(auth, db, phone))
    assert owner["user"]["id"] == uid

    with pytest.raises(UnauthorizedException):
        await auth.login(phone, "Attacker@123")
    with pytest.raises(UnauthorizedException):
        await auth.refresh(attacker["refresh_token"])
    # The owner's own session is the fresh one and still works.
    assert (await auth.refresh(owner["refresh_token"]))["access_token"]


async def test_a_second_proof_does_not_sign_the_owner_out(db, cleanup):
    phone = "9811100003"
    uid = await _squatted_account(db, cleanup, phone, "Attacker@123")
    auth = AuthService(db)
    first = await auth.otp_login(phone, otp=await _code(auth, db, phone))
    # Later proofs (another OTP login, a WhatsApp chat) only refresh the window.
    await db.otp_requests.delete_many({"identifier": phone})
    await auth.mark_phone_proven(await db.users.find_one({"_id": ObjectId(uid)}))
    assert (await auth.refresh(first["refresh_token"]))["access_token"]


def _stub_google(monkeypatch, email: str, sub: str):
    async def verified(self, credential):
        return {"sub": sub, "email": email, "email_verified": "true", "name": "G User"}

    monkeypatch.setattr(GoogleAuthService, "_verify_id_token", verified)


async def test_google_never_joins_a_password_account_or_a_staff_account(db, cleanup, monkeypatch):
    # A customer account someone set a password on, with the victim's email.
    now = datetime.now(timezone.utc)
    planted = await db.users.insert_one({
        "full_name": "Planted", "email": "victim.g@example.com", "phone": "9811100004",
        "password_hash": hash_password("Attacker@123"), "role": "customer", "status": "active",
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    cleanup.append(("users", {"_id": planted.inserted_id}))
    _stub_google(monkeypatch, "victim.g@example.com", "google-sub-victim")
    with pytest.raises(UnauthorizedException):
        await GoogleAuthService(db).login_with_google("credential")
    assert "google_sub" not in await db.users.find_one({"_id": planted.inserted_id})

    # A manager whose email is a Google account gets no staff tokens.
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    manager = await db.users.find_one({"_id": ObjectId(manager_id)})
    _stub_google(monkeypatch, manager["email"], "google-sub-manager")
    with pytest.raises(UnauthorizedException):
        await GoogleAuthService(db).login_with_google("credential")


async def test_google_still_signs_in_a_passwordless_customer(db, cleanup, monkeypatch):
    now = datetime.now(timezone.utc)
    existing = await db.users.insert_one({
        "full_name": "Quick Booker", "email": "quick.g@example.com", "phone": "9811100005",
        "password_hash": None, "role": "customer", "status": "active",
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    cleanup.append(("users", {"_id": existing.inserted_id}))
    _stub_google(monkeypatch, "quick.g@example.com", "google-sub-quick")
    result = await GoogleAuthService(db).login_with_google("credential")
    assert result["user"]["id"] == str(existing.inserted_id)


async def test_parallel_wrong_passwords_lock_the_account(db, cleanup):
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    manager = await db.users.find_one({"_id": ObjectId(manager_id)})
    auth = AuthService(db)

    results = await asyncio.gather(*(auth.login(manager["email"], "wrong-guess") for _ in range(20)), return_exceptions=True)
    assert all(isinstance(r, UnauthorizedException) for r in results)
    locked = await db.users.find_one({"_id": ObjectId(manager_id)})
    assert locked.get("login_locked_until") is not None
    # Even the right password waits out the lock.
    with pytest.raises(UnauthorizedException, match="Too many failed attempts"):
        await auth.login(manager["email"], "Test@12345")
