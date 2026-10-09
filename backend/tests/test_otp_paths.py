"""
Every backend OTP path, end to end — WhatsApp on the log provider and MSG91
stubbed, so nothing here reaches Meta or MSG91:
  - a code that fails to send is rolled back: no cooldown, not counted
    toward the hourly cap (so the client can fall back to the widget);
  - no reachable channel is refused before cooldown/cap are touched;
  - "+91 …", "0…", spaces and email-in-any-case all resolve to one code;
  - OTP login refuses staff numbers before spending a code;
  - single use and the attempt limit hold under parallel requests;
  - concurrent sends produce one code and one cap claim;
  - codes stored before the fixed _id still verify after deploy;
  - MSG91 down/slow is a retryable 503, never "wrong code"; tokens bind to
    the phone whatever its format;
  - add-phone reports a failed send instead of "Code sent".
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.exceptions import BadRequestException
from app.services import msg91_widget_service as widget_mod
from app.services.auth_service import AuthService
from app.services.msg91_widget_service import Msg91Unavailable, Msg91WidgetService
from app.services.whatsapp_service import WhatsAppService
from tests.factories import make_customer, make_manager

_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _track(cleanup, phone: str):
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))


async def _code(db, phone: str) -> str:
    return (await db.otp_requests.find_one({"identifier": phone}))["otp"]


async def _cap(db, phone: str) -> int:
    doc = await db.otp_requests.find_one({"_id": f"otp-send-cap:{phone}"})
    return doc["sent"] if doc else 0


def _fake_jwt(payload: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(payload)}.signature"


def _stub_msg91(monkeypatch, *, status=200, body=None, exc=None, delay=0.0):
    body = body if body is not None else {"type": "success", "message": "OTP verified"}

    class FakeResponse:
        status_code = status
        headers = {"content-type": "application/json"}

        def json(self):
            return body

    async def fake_post(self, access_token):
        if delay:
            await asyncio.sleep(delay)
        if exc:
            raise exc
        return FakeResponse()

    monkeypatch.setattr(Msg91WidgetService, "_post_verify", fake_post)
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", "test-auth-key")


def _flaky_whatsapp(monkeypatch):
    """WhatsApp send that fails while state["fail"] is set."""
    state = {"fail": True}
    original = WhatsAppService.send_otp

    async def send_otp(self, phone, code, purpose="verification"):
        if state["fail"]:
            return False
        return await original(self, phone, code, purpose)

    monkeypatch.setattr(WhatsAppService, "send_otp", send_otp)
    return state


@pytest.mark.asyncio
async def test_failed_send_is_rolled_back_not_capped_and_not_cooling_down(db, cleanup, monkeypatch):
    phone = "9722200001"
    _track(cleanup, phone)
    auth = AuthService(db)
    await auth.ensure_customer_by_phone(phone, "Roll Back")
    state = _flaky_whatsapp(monkeypatch)

    # More failures than the hourly cap allows sends — none may count.
    for _ in range(AuthService._OTP_MAX_SENDS_PER_WINDOW + 2):
        with pytest.raises(BadRequestException, match="Couldn't send the verification code"):
            await auth.request_otp(phone)
    assert await _cap(db, phone) == 0
    assert await db.otp_requests.find_one({"identifier": phone}) is None

    # The very next try works — no "Please wait" for a code that never went out.
    state["fail"] = False
    assert await auth.request_otp(phone) == "whatsapp"
    assert await _cap(db, phone) == 1
    assert await auth.verify_otp(phone, await _code(db, phone)) is True


@pytest.mark.asyncio
async def test_no_reachable_channel_is_refused_before_cooldown_and_cap(db, cleanup, monkeypatch):
    phone = "9722200002"
    _track(cleanup, phone)
    if not await db.whatsapp_templates.find_one({"name": "blussit_otp"}):
        cleanup.append(("whatsapp_templates", {"name": "blussit_otp", "_test": True}))
        await db.whatsapp_templates.insert_one({"name": "blussit_otp", "status": "APPROVED", "category": "AUTHENTICATION", "_test": True})
    # meta_cloud chosen but no token: the provider silently degrades to the
    # log provider, which must not count as delivering a real code.
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "meta_cloud")
    auth = AuthService(db)

    with pytest.raises(BadRequestException, match="Couldn't send the verification code"):
        await auth.request_phone_otp(phone)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 0
    assert await _cap(db, phone) == 0
    assert await db.otp_requests.find_one({"identifier": phone}) is None


@pytest.mark.asyncio
async def test_any_phone_format_resolves_to_one_code(db, cleanup, monkeypatch):
    monkeypatch.setattr(AuthService, "_OTP_RESEND_COOLDOWN_SECONDS", 0)
    phone = "9722200003"
    _track(cleanup, phone)
    await AuthService(db).ensure_customer_by_phone(phone, "Formats")

    async with _client() as client:
        sent = await client.post("/api/v1/auth/otp/request", json={"identifier": "+91 97222 00003"})
        assert sent.status_code == 200, sent.text
        assert sent.json()["data"]["channel"] == "whatsapp"
        code = await _code(db, phone)
        login = await client.post("/api/v1/auth/otp-login", json={"phone": "09722200003", "otp": f" {code}"})
        assert login.status_code == 200, login.text
        assert login.json()["data"]["user"]["phone"] == phone

        # The booking popup's backend channel, typed with a dash and +91.
        booking = await client.post("/api/v1/bookings/verify-phone/request", json={"phone": "+91-97222-00003"})
        assert booking.status_code == 200, booking.text
    code = await _code(db, phone)
    auth = AuthService(db)
    assert await auth.verify_phone_proof("919722200003", code, None) is True
    with pytest.raises(BadRequestException, match="Invalid or expired code"):
        await auth.require_phone_proof(phone, code, None)  # single use


@pytest.mark.asyncio
async def test_forgot_password_by_email_in_any_case_resets_via_phone_key(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    _track(cleanup, user["phone"])
    auth = AuthService(db)

    await auth.request_otp(f"  {user['email'].upper()} ", purpose="password_reset")
    code = await _code(db, user["phone"])  # keyed by the phone it went to
    await auth.reset_password(user["email"].upper(), code, "Fresh#Pass123")
    assert (await auth.login(user["phone"], "Fresh#Pass123"))["user"]["id"] == customer_id
    with pytest.raises(BadRequestException, match="Invalid or expired code"):
        await auth.reset_password(user["phone"], code, "Other#Pass123")


@pytest.mark.asyncio
async def test_otp_login_request_refuses_staff_and_unknown_numbers(db, cleanup):
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    manager_phone = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    _track(cleanup, manager_phone)

    async with _client() as client:
        staff = await client.post("/api/v1/auth/otp/request", json={"identifier": manager_phone})
        # A phone with no account is no longer refused (2026-10-09): the code
        # goes out and the account is created, after asking the name, once it
        # is proven (test_feat_first_login_name). An unknown EMAIL has no
        # phone to send to, so it still gets "No account found".
        unknown = await client.post("/api/v1/auth/otp/request", json={"identifier": "nobody.here@gmail.com"})
        new_number = await client.post("/api/v1/auth/otp/request", json={"identifier": "9722200099"})
        assert await db.whatsapp_outbox.count_documents({"phone": manager_phone}) == 0
        # Staff reset a password by code only once they've verified the
        # phone themselves while signed in (AUTH-01 / P0-2: a staff phone is
        # typed in by an admin — a code sent there proved nothing). Before
        # that: refused, nothing sent, "ask your admin".
        refused = await client.post("/api/v1/auth/forgot-password", json={"identifier": manager_phone})
        assert await db.whatsapp_outbox.count_documents({"phone": manager_phone}) == 0
        await db.users.update_one(
            {"_id": ObjectId(manager_id)},
            {"$set": {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "self_verified_phone": manager_phone}},
        )
        reset = await client.post("/api/v1/auth/forgot-password", json={"identifier": manager_phone})
    assert staff.status_code == 400 and "Staff login" in staff.json()["message"]
    assert unknown.status_code == 404 and "No account found" in unknown.json()["message"]
    assert new_number.status_code == 200 and new_number.json()["data"]["new_account"] is True
    _track(cleanup, "9722200099")
    assert refused.status_code == 400 and "ask your admin" in refused.json()["message"], refused.text
    assert reset.status_code == 200, reset.text
    assert await db.whatsapp_outbox.count_documents({"phone": manager_phone}) == 1


@pytest.mark.asyncio
async def test_right_code_submitted_twice_in_parallel_verifies_once(db, cleanup):
    phone = "9722200004"
    _track(cleanup, phone)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    code = await _code(db, phone)
    results = await asyncio.gather(*(auth.verify_otp(phone, code) for _ in range(6)))
    assert results.count(True) == 1


@pytest.mark.asyncio
async def test_attempt_limit_holds_under_parallel_guesses(db, cleanup):
    phone = "9722200005"
    _track(cleanup, phone)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    code = await _code(db, phone)
    wrong = "000000" if code != "000000" else "111111"
    assert not any(await asyncio.gather(*(auth.verify_otp(phone, wrong) for _ in range(12))))
    assert (await db.otp_requests.find_one({"identifier": phone}))["attempts"] == AuthService._OTP_MAX_ATTEMPTS
    assert await auth.verify_otp(phone, code) is False  # locked, even with the right code


@pytest.mark.asyncio
async def test_parallel_sends_make_one_code_and_one_cap_claim(db, cleanup):
    phone = "9722200006"
    _track(cleanup, phone)
    auth = AuthService(db)
    results = await asyncio.gather(*(auth.request_phone_otp(phone) for _ in range(5)), return_exceptions=True)
    assert results.count("whatsapp") == 1
    refused = [r for r in results if isinstance(r, BadRequestException)]
    assert len(refused) == 4 and all(r.message.startswith("Please wait") for r in refused)
    assert await _cap(db, phone) == 1
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1

    with pytest.raises(BadRequestException, match=r"^Please wait (29|30)s before requesting another code\.$"):
        await auth.request_phone_otp(phone)


@pytest.mark.asyncio
async def test_code_stored_before_the_fixed_id_still_verifies(db, cleanup):
    phone = "9722200007"
    _track(cleanup, phone)
    auth = AuthService(db)
    now = datetime.now(timezone.utc)
    legacy = {"identifier": phone, "otp": "482913", "expires_at": now + timedelta(minutes=5), "last_sent_at": now - timedelta(minutes=5), "attempts": 0}
    await db.otp_requests.insert_one(dict(legacy))
    assert await auth.verify_otp(phone, "482913") is True

    await db.otp_requests.insert_one(dict(legacy))
    await auth.request_phone_otp(phone)
    docs = await db.otp_requests.find({"identifier": phone}).to_list(None)
    assert [d["_id"] for d in docs] == [f"otp:{phone}"]


@pytest.mark.asyncio
async def test_msg91_outage_is_a_retryable_503_not_a_wrong_code(db, cleanup, monkeypatch):
    phone = "9722200008"
    _track(cleanup, phone)
    await AuthService(db).ensure_customer_by_phone(phone, "Widget Outage")
    token = _fake_jwt({"identifier": f"91{phone}"})

    async def login():
        async with _client() as client:
            return await client.post("/api/v1/auth/otp-login", json={"phone": phone, "access_token": token})

    _stub_msg91(monkeypatch, exc=httpx.ConnectTimeout("boom"))
    down = await login()
    assert down.status_code == 503 and down.json()["message"].startswith("Couldn't confirm the code")

    monkeypatch.setattr(widget_mod, "_VERIFY_DEADLINE_SECONDS", 0.05)
    _stub_msg91(monkeypatch, delay=1)
    slow = await login()
    assert slow.status_code == 503

    _stub_msg91(monkeypatch, status=502, body={})
    assert (await login()).status_code == 503

    _stub_msg91(monkeypatch, body={"type": "error", "message": "invalid token"})
    rejected = await login()
    assert rejected.status_code == 400 and rejected.json()["message"] == "Invalid or expired code."

    _stub_msg91(monkeypatch)
    ok = await login()
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_widget_token_binds_to_the_phone_whatever_its_format(db, cleanup, monkeypatch):
    phone = "9722200009"
    _track(cleanup, phone)
    auth = AuthService(db)
    await auth.ensure_customer_by_phone(phone, "Widget Formats")
    _stub_msg91(monkeypatch)
    mine = _fake_jwt({"identifier": f"91{phone}"})
    mine_again = _fake_jwt({"identifier": f"91{phone}", "n": 2})
    someone_else = _fake_jwt({"identifier": "919722200010"})

    assert await auth.verify_phone_proof("+91 97222 00009", None, mine) is True
    assert await auth.verify_phone_proof(phone, None, someone_else) is False
    with pytest.raises(BadRequestException):
        await auth.reset_password_widget(someone_else, phone, "Widget#Pass1")
    # Widget tokens are single use (AUTH-03): `mine` was spent above.
    with pytest.raises(BadRequestException):
        await auth.reset_password_widget(mine, phone, "Widget#Pass1")
    await auth.reset_password_widget(mine_again, "09722200009", "Widget#Pass1")
    assert (await auth.login(phone, "Widget#Pass1"))["user"]["phone"] == phone
    with pytest.raises(Msg91Unavailable):
        _stub_msg91(monkeypatch, exc=httpx.ReadTimeout("slow"))
        await auth.verify_phone_proof(phone, None, _fake_jwt({"identifier": f"91{phone}", "n": 3}))


@pytest.mark.asyncio
async def test_add_phone_reports_a_failed_send_then_works(db, cleanup, monkeypatch):
    phone = "9722200011"
    _track(cleanup, phone)
    now = datetime.now(timezone.utc)
    user_id = str((await db.users.insert_one({
        "full_name": "Google NoPhone", "email": "otp.paths.google@example.com", "password_hash": None,
        "role": "customer", "status": "active", "is_deleted": False, "created_at": now, "updated_at": now,
    })).inserted_id)
    cleanup.append(("users", {"_id": ObjectId(user_id)}))
    auth = AuthService(db)
    state = _flaky_whatsapp(monkeypatch)

    with pytest.raises(BadRequestException, match="Couldn't send the verification code"):
        await auth.add_phone_request(user_id, "+91 97222 00011")
    state["fail"] = False
    assert await auth.add_phone_request(user_id, "+91 97222 00011") == "whatsapp"
    updated = await auth.add_phone_confirm(user_id, "09722200011", otp=await _code(db, phone))
    assert updated["phone"] == phone and updated["phone_verified"] is True
