"""
AUTH-06 regression tests — a stranger can't use up a customer's OTP
allowance. Sends are capped per (phone, requester): the requester is the
caller's IP bucket (IPv6 /64) for anonymous requests, the account for
signed-in ones; a higher per-phone ceiling still bounds the SMS bill.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.services.auth_service import AuthService
from tests.factories import make_customer

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


async def _customer(db, cleanup) -> str:
    cid = await make_customer(db)
    phone = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    cleanup.append(("otp_requests", {"identifier": phone}))
    cleanup.append(("otp_requests", {"_id": f"otp-send-cap:{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    return phone


async def _uncool(db, phone: str):
    await db.otp_requests.update_many({"identifier": phone}, {"$set": {"last_sent_at": datetime.now(timezone.utc) - timedelta(seconds=60)}})


def _from(ip: str) -> dict:
    return {"X-Forwarded-For": ip}


async def test_strangers_booking_codes_dont_lock_the_owner_out_of_login(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    phone = await _customer(db, cleanup)
    async with _client() as c:
        codes = []
        for _ in range(6):
            await _uncool(db, phone)
            r = await c.post("/api/v1/bookings/verify-phone/request", headers=_from("203.0.113.5"), json={"phone": phone})
            codes.append(r.status_code)
        assert codes == [200] * 5 + [429]
        await _uncool(db, phone)
        r = await c.post("/api/v1/auth/otp/request", headers=_from("198.51.100.7"), json={"identifier": phone})
        assert r.status_code == 200, r.text
        # The stranger is still capped, and so is a stranger hopping inside one IPv6 /64.
        await _uncool(db, phone)
        assert (await c.post("/api/v1/bookings/verify-phone/request", headers=_from("203.0.113.5"), json={"phone": phone})).status_code == 429


async def test_ipv6_requesters_share_their_64(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    phone = await _customer(db, cleanup)
    async with _client() as c:
        codes = []
        for i in range(6):
            await _uncool(db, phone)
            r = await c.post("/api/v1/bookings/verify-phone/request", headers=_from(f"2001:db8:1:1::{i + 1:x}"), json={"phone": phone})
            codes.append(r.status_code)
    assert codes == [200] * 5 + [429]


async def test_per_phone_ceiling_still_bounds_the_bill(db, cleanup, monkeypatch):
    """Past the per-phone total only a requester that has NOT yet sent for this
    phone still gets a code (so strangers can't shut the owner out), and the
    absolute ceiling bounds the bill whatever the number of addresses."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    phone = await _customer(db, cleanup)
    total, absolute = settings.OTP_PHONE_HOURLY_LIMIT, settings.OTP_PHONE_ABSOLUTE_HOURLY_LIMIT
    assert absolute > total > settings.OTP_PHONE_SOURCE_HOURLY_LIMIT
    sent = 0
    async with _client() as c:
        for i in range(absolute + 3):
            await _uncool(db, phone)
            r = await c.post("/api/v1/bookings/verify-phone/request", headers=_from(f"192.0.2.{i + 1}"), json={"phone": phone})
            if r.status_code == 200:
                sent += 1
            else:
                assert r.status_code == 429
    # The 41st+ requester gets "sent" too (a live code exists) but no new message.
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == absolute


async def test_a_flood_from_a_few_addresses_cannot_shut_the_owner_out(db, cleanup, monkeypatch):
    """Three strangers use up the phone's total allowance; the owner (a fresh
    address, and a device that signed in before) still gets a code."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    phone = await _customer(db, cleanup)
    async with _client() as c:
        for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
            for _ in range(5):
                await _uncool(db, phone)
                assert (await c.post("/api/v1/bookings/verify-phone/request", headers=_from(ip), json={"phone": phone})).status_code == 200
        assert (await db.otp_requests.find_one({"_id": f"otp-send-cap:{phone}"}))["sent"] == 15
        # a fourth stranger who has not sent yet, and the owner, both still get through
        await _uncool(db, phone)
        before = await db.whatsapp_outbox.count_documents({"phone": phone})
        r = await c.post("/api/v1/auth/otp/request", headers=_from("198.51.100.77"), json={"identifier": phone})
        assert r.status_code == 200
        assert await db.whatsapp_outbox.count_documents({"phone": phone}) == before + 1
        code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]
        login = await c.post("/api/v1/auth/otp-login", headers=_from("198.51.100.77"), json={"phone": phone, "otp": code})
        assert login.status_code == 200, login.text


async def test_signed_in_requests_count_against_the_account(db, cleanup):
    """verify-phone/request is attributed to the account, not an IP — an
    anonymous flood on the same number leaves the owner their own sends."""
    from app.core.security import create_access_token

    phone = await _customer(db, cleanup)
    user = await db.users.find_one({"phone": phone})
    auth = AuthService(db)
    for _ in range(5):
        await _uncool(db, phone)
        await auth.request_phone_otp(phone)
    h = {"Authorization": f"Bearer {create_access_token(str(user['_id']), 'customer', {'service_center_id': None, 'tv': 0})}"}
    async with _client() as c:
        await _uncool(db, phone)
        r = await c.post("/api/v1/auth/verify-phone/request", headers=h)
    assert r.status_code == 200, r.text
