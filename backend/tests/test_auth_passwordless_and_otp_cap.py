"""
Passwordless customers + bcrypt off the event loop + per-phone OTP cap:
  - quick-booking / WhatsApp / Google customers are created with
    password_hash None (no ~250 ms hash of a password nobody can use);
  - password login for such an account is a plain 401, never a 500, and a
    normal password login still works;
  - they can still get a password through forgot-password;
  - the async bcrypt helpers keep the event loop running while hashing;
  - one phone gets at most 5 codes an hour across every OTP entry point,
    atomically, and consuming a code doesn't reset the allowance.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnauthorizedException
from app.core.security import hash_password_async, verify_password_async
from app.services import google_auth_service
from app.services.auth_service import AuthService
from app.services.google_auth_service import GoogleAuthService
from app.services.whatsapp_bot_service import WhatsAppBotService
from tests.factories import make_customer


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch_google(monkeypatch, body: dict):
    class FakeResponse:
        status_code = 200

        def json(self):
            return body

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr(google_auth_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")


def _track_phone(cleanup, phone: str):
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))


@pytest.mark.asyncio
async def test_quick_booking_customer_has_no_password_and_login_is_401(db, cleanup):
    phone = "9711100001"
    _track_phone(cleanup, phone)
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Quick NoPass")
    stored = await db.users.find_one({"_id": customer["_id"]})
    assert "password_hash" in stored and stored["password_hash"] is None

    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await AuthService(db).login(phone, "whatever-password")
    async with _client() as client:
        res = await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "whatever-password"})
    assert res.status_code == 401, res.text
    assert res.json()["message"] == "Invalid credentials"

    # Password-less -> the guest wizard asks for an OTP, never a password.
    assert (await AuthService(db).booking_access_mode(phone))["mode"] == "otp"


@pytest.mark.asyncio
async def test_whatsapp_created_customer_has_no_password(db, cleanup):
    phone = "9711100002"
    _track_phone(cleanup, phone)
    customer_id, created = await WhatsAppBotService(db)._ensure_customer(f"91{phone}", None, supplied_name="WA NoPass")
    assert created is True
    stored = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert stored["password_hash"] is None and stored["phone_verified"] is True
    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await AuthService(db).login(phone, "whatever-password")
    # Phone freshly verified by the chat, but still no password to ask for.
    assert (await AuthService(db).booking_access_mode(phone))["mode"] == "otp"


@pytest.mark.asyncio
async def test_google_created_customer_has_no_password(db, cleanup, monkeypatch):
    _patch_google(monkeypatch, {"aud": "test-client-id", "sub": "google-sub-nopass", "email": "nopass@example.com", "email_verified": "true", "name": "G NoPass"})
    cleanup.append(("users", {"google_sub": "google-sub-nopass"}))
    result = await GoogleAuthService(db).login_with_google("x" * 30)
    stored = await db.users.find_one({"_id": ObjectId(result["user"]["id"])})
    assert stored["password_hash"] is None

    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await AuthService(db).login("nopass@example.com", "whatever-password")
    with pytest.raises(BadRequestException, match="doesn't have a password yet"):
        await AuthService(db).change_password(result["user"]["id"], "whatever", "BrandNew#Pass1")


@pytest.mark.asyncio
async def test_password_login_still_works_and_wrong_or_malformed_is_401(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]

    tokens = await AuthService(db).login(phone, "Test@12345")
    assert tokens["user"]["id"] == customer_id
    async with _client() as client:
        good = await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Test@12345"})
        bad = await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Wrong@12345"})
    assert good.status_code == 200, good.text
    assert bad.status_code == 401, bad.text

    # A malformed legacy hash is a mismatch, not a crash.
    await db.users.update_one({"_id": ObjectId(customer_id)}, {"$set": {"password_hash": "x", "failed_login_attempts": 0}})
    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await AuthService(db).login(phone, "Test@12345")


@pytest.mark.asyncio
async def test_passwordless_customer_can_set_a_password_via_forgot_password(db, cleanup):
    phone = "9711100003"
    _track_phone(cleanup, phone)
    auth = AuthService(db)
    await auth.ensure_customer_by_phone(phone, "Later Password")

    await auth.request_otp(phone, purpose="password_reset")
    code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]
    await auth.reset_password(phone, code, "Chosen#Pass1")

    tokens = await auth.login(phone, "Chosen#Pass1")
    assert tokens["user"]["phone"] == phone
    await auth.change_password(tokens["user"]["id"], "Chosen#Pass1", "Second#Pass2")
    assert (await auth.login(phone, "Second#Pass2"))["user"]["phone"] == phone


@pytest.mark.asyncio
async def test_async_bcrypt_keeps_the_event_loop_running():
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    hashed = await hash_password_async("Loop#Pass1")
    assert await verify_password_async("Loop#Pass1", hashed) is True
    task.cancel()
    assert ticks >= 3  # inline bcrypt would have frozen the loop at 0
    assert await verify_password_async("Loop#Pass1", None) is False
    assert await verify_password_async("Loop#Pass1", "not-a-hash") is False


@pytest.mark.asyncio
async def test_hourly_otp_cap_per_phone_across_entry_points(db, cleanup, monkeypatch):
    monkeypatch.setattr(AuthService, "_OTP_RESEND_COOLDOWN_SECONDS", 0)
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    _track_phone(cleanup, phone)
    auth = AuthService(db)

    # Login OTP and the booking verify-phone request share one allowance.
    for _ in range(3):
        await auth.request_otp(phone)
    await auth.request_phone_otp(phone)
    await auth.request_phone_otp(phone)

    # Consuming the code must not reset the count.
    code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]
    assert await auth.verify_otp(phone, code) is True

    with pytest.raises(BadRequestException, match="Too many codes requested for this number — try again in 60 minutes"):
        await auth.request_otp(phone)
    with pytest.raises(BadRequestException, match="Too many codes"):
        await auth.request_phone_otp(phone)
    sent = await db.whatsapp_outbox.count_documents({"phone": phone})
    assert sent == 5

    # Window over (TTL sweep not yet run) -> a fresh allowance.
    await db.otp_requests.update_one(
        {"_id": f"otp-send-cap:{phone}"}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}
    )
    await auth.request_otp(phone)
    assert (await db.otp_requests.find_one({"_id": f"otp-send-cap:{phone}"}))["sent"] == 1


@pytest.mark.asyncio
async def test_otp_cap_holds_under_a_concurrent_burst(db, cleanup):
    phone = "9711100004"
    _track_phone(cleanup, phone)
    auth = AuthService(db)

    results = await asyncio.gather(*(auth._claim_otp_send(phone) for _ in range(12)), return_exceptions=True)
    allowed = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, BadRequestException)]
    assert len(allowed) == AuthService._OTP_MAX_SENDS_PER_WINDOW
    assert len(refused) == 12 - AuthService._OTP_MAX_SENDS_PER_WINDOW
    assert all("Too many codes" in r.message for r in refused)
