"""
Google sign-in + the phone-mandatory-at-booking rule:
  - a verified Google token creates a phoneless customer (or links by
    email / matches by sub on later logins);
  - wrong audience or unverified email is rejected;
  - a phoneless Google customer CANNOT book until they attach + verify a
    number (add-phone flow), and a number already on another account is
    refused.
"""
import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import BadRequestException, PhoneNotVerifiedException
from app.services import google_auth_service as mod
from app.services.auth_service import AuthService
from app.services.google_auth_service import GoogleAuthService

from tests.factories import make_customer


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def patch_google(monkeypatch, body, status=200):
    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            return FakeResponse(status, body)

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")


GOOD = {"aud": "test-client-id", "sub": "google-sub-001", "email": "gtest@example.com", "email_verified": "true", "name": "G Tester"}


@pytest.mark.asyncio
async def test_google_login_creates_phoneless_customer_then_matches_by_sub(db, cleanup, monkeypatch):
    patch_google(monkeypatch, GOOD)
    cleanup.append(("users", {"google_sub": "google-sub-001"}))
    svc = GoogleAuthService(db)

    first = await svc.login_with_google("x" * 30)
    user = first["user"]
    assert user["role"] == "customer" and user["phone"] is None
    assert user["phone_verification_stale"] is True  # must add a phone before booking
    assert user["must_change_password"] is False  # no password gate for Google users

    second = await svc.login_with_google("x" * 30)
    assert second["user"]["id"] == user["id"]  # matched, not duplicated
    assert await db.users.count_documents({"google_sub": "google-sub-001"}) == 1


@pytest.mark.asyncio
async def test_wrong_audience_rejected(db, monkeypatch):
    patch_google(monkeypatch, {**GOOD, "aud": "someone-elses-client"})
    from app.core.exceptions import UnauthorizedException

    with pytest.raises(UnauthorizedException):
        await GoogleAuthService(db).login_with_google("x" * 30)


@pytest.mark.asyncio
async def test_phoneless_customer_blocked_from_booking_until_phone_added(db, cleanup, monkeypatch):
    patch_google(monkeypatch, {**GOOD, "sub": "google-sub-002", "email": "gtest2@example.com"})
    cleanup.append(("users", {"google_sub": "google-sub-002"}))
    cleanup.append(("otp_requests", {"identifier": "9777766001"}))
    svc = GoogleAuthService(db)
    result = await svc.login_with_google("x" * 30)
    uid = result["user"]["id"]

    # The booking gate treats a phoneless account exactly like unverified.
    user_doc = await db.users.find_one({"_id": ObjectId(uid)})
    assert AuthService.phone_verification_fresh(user_doc) is False

    # Attach + verify the primary contact number.
    auth = AuthService(db)
    await auth.add_phone_request(uid, "9777766001")
    code = (await db.otp_requests.find_one({"identifier": "9777766001"}))["otp"]
    with pytest.raises(BadRequestException):
        await auth.add_phone_confirm(uid, "9777766001", otp="000000")
    await auth.add_phone_confirm(uid, "9777766001", otp=code)

    fresh = await db.users.find_one({"_id": ObjectId(uid)})
    assert fresh["phone"] == "9777766001"
    assert AuthService.phone_verification_fresh(fresh) is True


@pytest.mark.asyncio
async def test_add_phone_refuses_numbers_owned_by_other_accounts(db, cleanup, monkeypatch):
    patch_google(monkeypatch, {**GOOD, "sub": "google-sub-003", "email": "gtest3@example.com"})
    cleanup.append(("users", {"google_sub": "google-sub-003"}))
    other_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(other_id)}))
    other = await db.users.find_one({"_id": ObjectId(other_id)})

    result = await GoogleAuthService(db).login_with_google("x" * 30)
    with pytest.raises(BadRequestException, match="already used"):
        await AuthService(db).add_phone_request(result["user"]["id"], other["phone"])
