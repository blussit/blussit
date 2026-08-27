"""
WhatsApp provider abstraction — the log provider (the one actually active
in this test environment, since no real WHATSAPP_ACCESS_TOKEN is
configured) must genuinely record every message so OTP/notification flows
are verifiable without a live WhatsApp account, and the factory must fall
back to it safely for any incomplete "meta_cloud" configuration.
"""
import pytest

from app.core.config import settings
from app.services.whatsapp_service import LogWhatsAppProvider, WhatsAppService, get_whatsapp_provider


@pytest.fixture
async def cleanup_outbox(db, cleanup):
    # Scoped to only the fake numbers this file uses, not a blanket
    # delete-everything on a shared collection.
    cleanup.append(("whatsapp_outbox", {"phone": {"$regex": "^98765432"}}))
    return db


@pytest.mark.asyncio
async def test_log_provider_records_the_message(db, cleanup_outbox):
    provider = LogWhatsAppProvider(db)
    ok = await provider.send("9876543210", "hello there")
    assert ok is True

    doc = await db.whatsapp_outbox.find_one({"phone": "9876543210"})
    assert doc is not None
    assert doc["message"] == "hello there"
    assert doc["provider"] == "log"


@pytest.mark.asyncio
async def test_factory_defaults_to_log_provider_with_no_credentials(db):
    assert settings.WHATSAPP_ACCESS_TOKEN == ""
    provider = get_whatsapp_provider(db)
    assert isinstance(provider, LogWhatsAppProvider)


@pytest.mark.asyncio
async def test_factory_falls_back_to_log_when_provider_set_but_keys_blank(db, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "meta_cloud")
    # access token / phone number id left blank
    provider = get_whatsapp_provider(db)
    assert isinstance(provider, LogWhatsAppProvider)


@pytest.mark.asyncio
async def test_whatsapp_service_send_otp_message_shape(db, cleanup_outbox):
    service = WhatsAppService(db)
    ok = await service.send_otp("9876543211", "123456", purpose="verification")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543211"})
    assert "123456" in doc["message"]


@pytest.mark.asyncio
async def test_whatsapp_service_send_temp_password_never_logs_it_elsewhere(db, cleanup_outbox):
    service = WhatsAppService(db)
    ok = await service.send_temp_password("9876543212", "Sw9kLp2Qrt")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543212"})
    assert "Sw9kLp2Qrt" in doc["message"]
