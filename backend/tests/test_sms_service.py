"""
SMS OTP channel — the bridge while WhatsApp auth templates await business
verification. Channel-order + fallback behavior is the part that matters:
OTP_CHANNEL picks who goes first, the other channel catches failures, and
SMS disabled (the default) must degrade to exactly the old WhatsApp-only
behavior.
"""
import pytest
from bson import ObjectId

from app.core.config import settings
from app.services.auth_service import AuthService
from app.services.sms_service import Fast2SmsProvider, LogSmsProvider, SmsService, get_sms_provider

from tests.factories import make_customer


@pytest.fixture
async def customer(db, cleanup):
    customer_id = await make_customer(db, phone_verified=False)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    doc = await db.users.find_one({"_id": ObjectId(customer_id)})
    cleanup.append(("otp_requests", {"identifier": doc["phone"]}))
    cleanup.append(("whatsapp_outbox", {"phone": doc["phone"]}))
    cleanup.append(("sms_outbox", {"phone": doc["phone"]}))
    return customer_id, doc["phone"]


@pytest.mark.asyncio
async def test_sms_disabled_by_default_keeps_whatsapp_only_behavior(db, customer):
    assert settings.SMS_PROVIDER == ""
    assert get_sms_provider(db) is None
    customer_id, phone = customer
    await AuthService(db).request_phone_verification(customer_id)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1
    assert await db.sms_outbox.count_documents({"phone": phone}) == 0


@pytest.mark.asyncio
async def test_otp_channel_sms_sends_via_sms_first(db, customer, monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")
    monkeypatch.setattr(settings, "OTP_CHANNEL", "sms")
    customer_id, phone = customer
    await AuthService(db).request_phone_verification(customer_id)
    sms = await db.sms_outbox.find_one({"phone": phone})
    otp_doc = await db.otp_requests.find_one({"identifier": phone})
    assert sms is not None and otp_doc["otp"] in sms["message"]
    # WhatsApp untouched — SMS succeeded first.
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 0


@pytest.mark.asyncio
async def test_sms_first_falls_back_to_whatsapp_when_sms_fails(db, customer, monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")
    monkeypatch.setattr(settings, "OTP_CHANNEL", "sms")

    async def failing_send_otp(self, phone, code):
        return False

    monkeypatch.setattr(LogSmsProvider, "send_otp", failing_send_otp)
    customer_id, phone = customer
    await AuthService(db).request_phone_verification(customer_id)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1  # fallback fired


@pytest.mark.asyncio
async def test_fast2sms_provider_refuses_free_text_without_dlt(db):
    provider = Fast2SmsProvider(db, "fake-key")
    assert await provider.send_text("9876500001", "Your temp password is Xy12") is False


@pytest.mark.asyncio
async def test_sms_service_temp_password_uses_free_text_route(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")
    cleanup.append(("sms_outbox", {"phone": "9876500002"}))
    service = SmsService(db)
    assert service.enabled
    ok = await service.send_temp_password("9876500002", "Ab12Cd34Ef")
    assert ok is True
    doc = await db.sms_outbox.find_one({"phone": "9876500002"})
    assert "Ab12Cd34Ef" in doc["message"]
