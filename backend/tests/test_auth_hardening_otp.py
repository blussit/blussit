"""
Auth hardening (founder rules 2026-10-28): OTP request rules (60 s per phone,
the SAME live code is re-sent, per-source escalating holds, a platform message
budget), WhatsApp-first with an SMS fallback / SMS button carrying the same
code, and no account enumeration. Log providers only — nothing leaves the
machine.
"""
import itertools
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core import rate_limit
from app.core.config import settings
from app.core.exceptions import BadRequestException, TooManyRequestsException
from app.services import auth_guard as guard
from app.services.auth_service import AuthService
from app.services.whatsapp_bot_service import WhatsAppBotService
from app.services.whatsapp_service import WhatsAppService, undeliverable_code
from tests.factories import make_customer, make_manager

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient
_PHONES = itertools.count(9755500001)


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def _as(ip: str | None, device: str | None):
    rate_limit._request_client.set(ip)
    rate_limit._request_device.set(device)


@pytest.fixture(autouse=True)
def _forget_client():
    yield
    _as(None, None)


@pytest.fixture
def controls(monkeypatch, db):
    """The source holds + message budget are on in production (RATE_LIMIT_ENABLED)."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


def _phone(cleanup) -> str:
    phone = str(next(_PHONES))
    _track(cleanup, phone)
    return phone


def _track(cleanup, phone: str):
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("sms_outbox", {"phone": phone}))
    cleanup.append(("rate_counters", {"_id": {"$regex": "^(otp|login)"}}))
    cleanup.append(("abuse_holds", {}))
    cleanup.append(("users", {"phone": phone}))


async def _doc(db, phone: str) -> dict | None:
    return await db.otp_requests.find_one({"_id": f"otp:{phone}"})


async def _uncool(db, phone: str):
    await db.otp_requests.update_many({"identifier": phone}, {"$set": {"last_sent_at": datetime.now(timezone.utc) - timedelta(seconds=61)}})


# --------------------------------------------------------- 2a: cooldown + same code


async def test_one_request_per_phone_per_60_seconds_with_a_countdown(db, cleanup):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    async with _client() as c:
        r = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": phone})
    assert r.status_code == 429
    body = r.json()
    assert body["error_code"] == "OTP_COOLDOWN" and 55 <= body["details"]["retry_after"] <= 60
    assert body["message"].startswith("Try again in ")
    assert settings.OTP_RESEND_COOLDOWN_SECONDS == 60


async def test_a_resend_inside_the_validity_window_sends_the_same_code(db, cleanup):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    first = await _doc(db, phone)
    await _uncool(db, phone)
    await auth.request_phone_otp(phone)  # a login-purpose request later
    again = await _doc(db, phone)
    assert again["otp"] == first["otp"] and again["expires_at"] == first["expires_at"]
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 2  # sent twice, same code
    # Both purposes now take it.
    assert await auth.verify_phone_proof(phone, first["otp"], None) is True


async def test_a_stranger_cannot_invalidate_the_owners_live_code(db, cleanup):
    phone = _phone(cleanup)
    auth = AuthService(db)
    _as("198.51.100.1", None)
    await auth.request_phone_otp(phone)  # the owner asked
    owner_code = (await _doc(db, phone))["otp"]
    for ip in ("203.0.113.1", "203.0.113.2"):  # strangers keep asking for that number
        _as(ip, None)
        await _uncool(db, phone)
        await auth.request_phone_otp(phone)
        assert (await _doc(db, phone))["otp"] == owner_code
    _as("198.51.100.1", None)
    assert await auth.verify_phone_proof(phone, owner_code, None) is True


async def test_a_stranger_burning_guesses_leaves_the_owner_theirs(db, cleanup):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    code = (await _doc(db, phone))["otp"]
    wrong = "000000" if code != "000000" else "111111"
    _as("203.0.113.1", None)
    for _ in range(9):
        await auth.verify_otp(phone, wrong)
    assert (await _doc(db, phone))["attempts"] == AuthService._OTP_MAX_ATTEMPTS_PER_REQUESTER
    _as("198.51.100.1", None)
    assert await auth.verify_otp(phone, code) is True


async def test_a_new_code_only_when_none_is_live(db, cleanup):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    first = (await _doc(db, phone))["otp"]
    assert await auth.verify_otp(phone, first) is True  # consumed
    await _uncool(db, phone)
    await auth.request_phone_otp(phone)
    assert await _doc(db, phone) is not None
    await db.otp_requests.update_one({"_id": f"otp:{phone}"}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1), "last_sent_at": datetime.now(timezone.utc) - timedelta(minutes=11)}})
    await auth.request_phone_otp(phone)
    assert (await _doc(db, phone))["attempts"] == 0


# ------------------------------------------------ 2b: per-source holds that escalate


async def test_six_requests_an_hour_from_one_device_start_an_escalating_hold(db, cleanup, controls):
    auth = AuthService(db)
    _as("203.0.113.5", "d" * 24)
    for _ in range(settings.OTP_SOURCE_HOURLY_LIMIT):
        await auth.request_phone_otp(_phone(cleanup))
    expected = [300, 1800, 7200, 86400]
    for level, seconds in enumerate(expected, start=1):
        with pytest.raises(TooManyRequestsException) as hit:
            await auth.request_phone_otp(_phone(cleanup))
        assert hit.value.error_code == "OTP_HELD"
        assert seconds - 5 <= hit.value.details["retry_after"] <= seconds
        # While held, even a first request is refused at once, 429 with retry_after.
        with pytest.raises(TooManyRequestsException) as still:
            await auth.request_phone_otp(_phone(cleanup))
        assert still.value.error_code == "OTP_HELD"
        hold = await db.abuse_holds.find_one({"_id": f"otp:dev:{guard.key_hash('d' * 24)}"})
        assert hold["level"] == level
        # The hold ends; the very next burst earns the next, longer one.
        await db.abuse_holds.update_one({"_id": hold["_id"]}, {"$set": {"until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        for _ in range(settings.OTP_SOURCE_HOURLY_LIMIT):
            await auth.request_phone_otp(_phone(cleanup))


async def test_the_ip_has_its_own_roomier_allowance(db, cleanup, controls, monkeypatch):
    monkeypatch.setattr(settings, "OTP_IP_HOURLY_LIMIT", 8)
    auth = AuthService(db)
    sent = 0
    with pytest.raises(TooManyRequestsException) as hit:
        for i in range(20):  # a new device every time: only the IP can stop this
            _as("203.0.113.6", f"{i:02d}" * 12)
            await auth.request_phone_otp(_phone(cleanup))
            sent += 1
    assert sent == 8 and hit.value.error_code == "OTP_HELD"
    # Another address is unaffected.
    _as("203.0.113.7", "z" * 24)
    await auth.request_phone_otp(_phone(cleanup))


async def test_requests_without_a_device_id_get_the_strict_per_source_number(db, cleanup, controls):
    auth = AuthService(db)
    _as("203.0.113.8", None)
    for _ in range(settings.OTP_SOURCE_HOURLY_LIMIT):
        await auth.request_phone_otp(_phone(cleanup))
    with pytest.raises(TooManyRequestsException, match="Too many code requests"):
        await auth.request_phone_otp(_phone(cleanup))


async def test_requests_refused_by_the_cooldown_do_not_count_against_the_source(db, cleanup, controls):
    auth = AuthService(db)
    _as("203.0.113.9", "e" * 24)
    phone = _phone(cleanup)
    await auth.request_phone_otp(phone)
    for _ in range(10):
        with pytest.raises(TooManyRequestsException) as hit:
            await auth.request_phone_otp(phone)
        assert hit.value.error_code == "OTP_COOLDOWN"
    await auth.request_phone_otp(_phone(cleanup))  # still allowed


# ----------------------------------------------- 2c: a stranger can't shut the owner out


async def test_a_trusted_owner_device_is_not_counted_out_by_strangers(db, cleanup):
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    user = await db.users.find_one({"_id": ObjectId(cid)})
    phone = user["phone"]
    _track(cleanup, phone)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"trusted_devices": [guard.device_hash("o" * 24)]}})
    auth = AuthService(db)
    _as("203.0.113.40", "s" * 24)
    await auth.request_otp(phone, customer_only=True)  # a stranger's request: a code is live now
    # Strangers from many addresses have used up the phone's whole allowance.
    await db.otp_requests.update_one({"_id": f"otp-send-cap:{phone}"}, {"$set": {"sent": settings.OTP_PHONE_ABSOLUTE_HOURLY_LIMIT}})
    await _uncool(db, phone)
    before = await db.whatsapp_outbox.count_documents({"phone": phone})
    _as("203.0.113.41", "t" * 24)  # one more stranger: answered as sent, but no new message
    assert await auth.request_otp(phone, customer_only=True) == "whatsapp"
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == before
    _as("198.51.100.40", "o" * 24)  # the owner's own device still gets its message
    await auth.request_otp(phone, customer_only=True)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == before + 1


# -------------------------------------------------------------- 2d: global budget


async def test_the_global_budget_alerts_once_and_slows_only_new_sources(db, cleanup, controls, monkeypatch, caplog):
    monkeypatch.setattr(settings, "OTP_GLOBAL_HOURLY_BUDGET", 3)
    admin = await db.users.find_one({"role": "admin"})
    now = datetime.now(timezone.utc)
    await db.rate_counters.delete_many({"_id": {"$regex": "^otp-budget"}})
    cleanup.append(("notifications", {"title": "Verification Codes Over Budget"}))
    auth = AuthService(db)
    caplog.set_level("ERROR")
    for i in range(3):
        _as(f"192.0.2.{i + 1}", f"{i}" * 24)
        await auth.request_phone_otp(_phone(cleanup))
    assert not await guard.budget_over(db) is False  # exactly at the budget: now over
    before = await db.notifications.count_documents({"title": "Verification Codes Over Budget", "user_id": str(admin["_id"])})
    _as("192.0.2.4", "4" * 24)
    live_phone = _phone(cleanup)
    await auth.request_phone_otp(live_phone)  # the 4th message crosses it
    assert any("OTP message budget crossed" in r.message for r in caplog.records)
    after = await db.notifications.count_documents({"title": "Verification Codes Over Budget", "user_id": str(admin["_id"])})
    assert after == before + 1
    # More messages do not alert again.
    _as("192.0.2.5", "5" * 24)
    await auth.request_phone_otp(_phone(cleanup))
    assert await db.notifications.count_documents({"title": "Verification Codes Over Budget", "user_id": str(admin["_id"])}) == after

    # A source may start ONE new code per cooldown while over budget...
    _as("192.0.2.6", "6" * 24)
    await auth.request_phone_otp(_phone(cleanup))
    with pytest.raises(TooManyRequestsException) as busy:
        await auth.request_phone_otp(_phone(cleanup))
    assert busy.value.error_code == "OTP_BUSY"
    # ...but a user mid-flow (a live code) can still have it re-sent.
    _as("192.0.2.4", "4" * 24)
    await _uncool(db, live_phone)
    await auth.request_phone_otp(live_phone)
    assert now  # silence linters


# ---------------------------------------------------------- 3: WhatsApp first, SMS fallback


@pytest.fixture
def sms_on(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")


async def test_sms_button_sends_the_same_live_code_by_sms(db, cleanup, sms_on):
    phone = _phone(cleanup)
    auth = AuthService(db)
    assert await auth.request_phone_otp(phone) == "whatsapp"
    code = (await _doc(db, phone))["otp"]
    await _uncool(db, phone)
    assert await auth.request_phone_otp(phone, channel="sms") == "sms"
    assert (await _doc(db, phone))["otp"] == code
    sms = await db.sms_outbox.find({"phone": phone}).to_list(None)
    assert len(sms) == 1 and sms[0]["kind"] == "otp"
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1  # no second WhatsApp
    assert (await _doc(db, phone))["channel"] == "sms"
    assert await auth.verify_phone_proof(phone, code, None) is True  # the same input accepts it


async def test_sms_channel_over_http_is_validated_counted_and_refused_when_sms_is_off(db, cleanup):
    phone = _phone(cleanup)
    async with _client() as c:
        bad = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": phone, "channel": "pigeon"})
        assert bad.status_code == 422
        off = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": phone, "channel": "sms"})
        assert off.status_code == 400 and "SMS" in off.json()["message"]
        assert await _doc(db, phone) is None  # nothing was claimed or stored
        ok = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": phone})
        assert ok.json()["data"] == {"otp_sent": True, "channel": "whatsapp", "cooldown_seconds": 60}


async def test_sms_requests_count_against_the_same_limits(db, cleanup, sms_on):
    phone = _phone(cleanup)
    auth = AuthService(db)
    _as("203.0.113.11", None)
    for i in range(settings.OTP_PHONE_SOURCE_HOURLY_LIMIT):
        await _uncool(db, phone)
        await auth.request_phone_otp(phone, channel="sms" if i % 2 else "whatsapp")
    await _uncool(db, phone)
    with pytest.raises(TooManyRequestsException):
        await auth.request_phone_otp(phone, channel="sms")


async def test_whatsapp_that_cannot_send_falls_back_to_sms_with_the_same_code(db, cleanup, sms_on, monkeypatch):
    phone = _phone(cleanup)

    async def refuse(self, phone, code, purpose="verification"):
        return False

    monkeypatch.setattr(WhatsAppService, "send_otp", refuse)
    auth = AuthService(db)
    assert await auth.request_phone_otp(phone) == "sms"
    assert await db.sms_outbox.count_documents({"phone": phone}) == 1
    assert (await _doc(db, phone))["channel"] == "sms"


def _status_hook(wamid: str, code: int, status: str = "failed") -> dict:
    return {"entry": [{"changes": [{"field": "messages", "value": {"statuses": [
        {"id": wamid, "status": status, "recipient_id": "91x", "errors": [{"code": code, "title": "Message undeliverable"}] if status == "failed" else []}]}}]}]}


async def _wamid(db, phone: str, wamid: str):
    await db.whatsapp_outbox.update_one({"phone": phone, "kind": "otp"}, {"$set": {"wamid": wamid}})


async def test_undeliverable_delivery_status_triggers_one_sms_with_the_same_code(db, cleanup, sms_on):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    code = (await _doc(db, phone))["otp"]
    await _wamid(db, phone, f"wamid.{phone}")
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(_status_hook(f"wamid.{phone}", 131026))
    sms = await db.sms_outbox.find({"phone": phone}).to_list(None)
    assert len(sms) == 1
    doc = await _doc(db, phone)
    assert doc["otp"] == code and doc["channel"] == "sms" and doc["sms_fallback"] is True
    await bot.handle_webhook(_status_hook(f"wamid.{phone}", 131026))  # Meta redelivers: still one
    assert await db.sms_outbox.count_documents({"phone": phone}) == 1
    assert await auth.verify_phone_proof(phone, code, None) is True


async def test_other_failures_stale_ones_and_missing_sms_send_nothing(db, cleanup, sms_on, monkeypatch):
    phone = _phone(cleanup)
    auth = AuthService(db)
    await auth.request_phone_otp(phone)
    await _wamid(db, phone, f"wamid.a{phone}")
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(_status_hook(f"wamid.a{phone}", 131000))  # not a definite undeliverable
    await bot.handle_webhook(_status_hook(f"wamid.a{phone}", 131026, status="delivered"))
    assert await db.sms_outbox.count_documents({"phone": phone}) == 0

    # A failure for an OLDER send (a newer code went out since) is ignored.
    await db.whatsapp_outbox.update_one({"wamid": f"wamid.a{phone}"}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(minutes=5)}})
    await bot.handle_webhook(_status_hook(f"wamid.a{phone}", 131026))
    assert await db.sms_outbox.count_documents({"phone": phone}) == 0

    # SMS not configured: detect-and-fall-back has nothing to fall back to.
    await db.whatsapp_outbox.update_one({"wamid": f"wamid.a{phone}"}, {"$set": {"created_at": datetime.now(timezone.utc)}})
    monkeypatch.setattr(settings, "SMS_PROVIDER", "")
    await bot.handle_webhook(_status_hook(f"wamid.a{phone}", 131026))
    assert await db.sms_outbox.count_documents({"phone": phone}) == 0


async def test_undeliverable_codes_are_recognised():
    assert undeliverable_code([{"code": 131026}]) == 131026
    assert undeliverable_code([{"code": "133010"}]) == 133010
    assert undeliverable_code([{"code": 132018}]) is None and undeliverable_code(None) is None


# ---------------------------------------------------------------- 4: no enumeration


async def test_booking_popup_answers_customer_unknown_and_staff_numbers_identically(db, cleanup):
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    customer = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    staff = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    unknown = _phone(cleanup)
    for p in (customer, staff):
        _track(cleanup, p)
    async with _client() as c:
        replies = [await c.post("/api/v1/bookings/verify-phone/request", json={"phone": p}) for p in (customer, staff, unknown)]
    assert all(r.status_code == 200 for r in replies)
    assert replies[0].json() == replies[1].json() == replies[2].json()
    assert await db.whatsapp_outbox.count_documents({"phone": staff}) == 0
    assert await db.whatsapp_outbox.count_documents({"phone": customer}) == 1
    # Even the staff member's own live code can't sign anyone in through a customer path.
    await db.otp_requests.update_one(
        {"_id": f"otp:{staff}"}, {"$set": {"otp": "424242", "fake": False, "purposes": ["verification"], "attempts": 0}}
    )
    async with _client() as c:
        out = await c.post("/api/v1/auth/otp-login", json={"phone": staff, "otp": "424242"})
        wrong = await c.post("/api/v1/auth/otp-login", json={"phone": unknown, "otp": "000000"})
    assert out.status_code == 400 and out.json()["message"] == "Invalid or expired code."
    assert wrong.status_code == 400 and wrong.json()["message"] == out.json()["message"]


async def test_cooldown_and_caps_behave_the_same_for_a_staff_number(db, cleanup):
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    staff = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    _track(cleanup, staff)
    async with _client() as c:
        first = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": staff})
        second = await c.post("/api/v1/bookings/verify-phone/request", json={"phone": staff})
    assert first.status_code == 200 and second.status_code == 429 and second.json()["error_code"] == "OTP_COOLDOWN"


async def test_a_pretend_send_takes_about_as_long_as_a_real_one(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "OTP_FAKE_SEND_DELAY_MS", 300)
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    staff = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    _track(cleanup, staff)
    started = time.monotonic()
    await AuthService(db).request_phone_otp(staff)
    assert time.monotonic() - started >= 0.2


async def test_register_checks_the_proof_before_it_says_anything_about_existing_accounts(db, cleanup):
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    taken = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    free = _phone(cleanup)
    async with _client() as c:
        replies = [
            await c.post("/api/v1/auth/register", json={"full_name": "Someone Else", "phone": p, "password": "Sup3r#Secret"})
            for p in (taken, free)
        ]
    assert replies[0].status_code == replies[1].status_code == 400
    assert replies[0].json() == replies[1].json()


async def test_reset_password_failures_all_read_the_same(db, cleanup):
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    real = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    staff = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    async with _client() as c:
        messages = set()
        for ident in (real, staff, "9000000001", "ghost@example.com"):
            r = await c.post("/api/v1/auth/reset-password", json={"identifier": ident, "otp": "123456", "new_password": "Another#Pass1"})
            assert r.status_code == 400
            messages.add(r.json()["message"])
    assert messages == {"Invalid or expired code."}


async def test_signed_in_verify_phone_still_sends_to_staff_themselves(db, cleanup):
    """The staff self-verification path (how staff get code-reset) is unchanged."""
    from app.core.security import create_access_token

    manager_id = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    staff = await db.users.find_one({"_id": ObjectId(manager_id)})
    _track(cleanup, staff["phone"])
    h = {"Authorization": f"Bearer {create_access_token(manager_id, 'manager', {'service_center_id': staff.get('service_center_id'), 'tv': staff.get('token_version', 0)})}"}
    async with _client() as c:
        r = await c.post("/api/v1/auth/verify-phone/request", headers=h)
        assert r.status_code == 200 and r.json()["data"]["channel"] == "whatsapp"
    assert await db.whatsapp_outbox.count_documents({"phone": staff["phone"]}) == 1
    assert (await _doc(db, staff["phone"]))["fake"] is False
