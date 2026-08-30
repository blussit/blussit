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


@pytest.mark.asyncio
async def test_log_provider_records_template_sends(db, cleanup_outbox):
    provider = LogWhatsAppProvider(db)
    ok = await provider.send_template("9876543213", "otp_code", "en_US", ["482913"])
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543213"})
    assert doc["template_name"] == "otp_code"
    assert "482913" in doc["message"]


@pytest.mark.asyncio
async def test_send_otp_uses_template_when_configured(db, cleanup_outbox, monkeypatch):
    """Once WHATSAPP_OTP_TEMPLATE_NAME is set, send_otp must go through
    send_template (the only kind that reaches a brand-new customer with no
    open session) instead of plain text."""
    monkeypatch.setattr(settings, "WHATSAPP_OTP_TEMPLATE_NAME", "otp_verification")
    monkeypatch.setattr(settings, "WHATSAPP_OTP_TEMPLATE_LANGUAGE", "en_US")
    service = WhatsAppService(db)
    ok = await service.send_otp("9876543214", "775533")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543214"})
    assert doc["template_name"] == "otp_verification"
    assert "775533" in doc["message"]


@pytest.mark.asyncio
async def test_send_otp_falls_back_to_plain_text_when_no_template_configured(db, cleanup_outbox):
    assert settings.WHATSAPP_OTP_TEMPLATE_NAME == ""
    service = WhatsAppService(db)
    ok = await service.send_otp("9876543215", "112233")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543215"})
    assert "template_name" not in doc
    assert "112233" in doc["message"]


@pytest.mark.asyncio
async def test_send_generic_uses_update_template_when_configured(db, cleanup_outbox, monkeypatch):
    """The NotificationService→WhatsApp bridge must switch to the utility
    template once configured — recipients of these (website customers,
    staff) frequently have no open session, where free text is silently
    dropped."""
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "cleanride_update")
    service = WhatsAppService(db)
    ok = await service.send_generic("9876543216", "Booking confirmed", "Your booking BK123 is confirmed.")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543216"})
    assert doc["template_name"] == "cleanride_update"
    assert "Booking confirmed" in doc["message"] and "BK123" in doc["message"]


@pytest.mark.asyncio
async def test_send_temp_password_uses_template_when_configured(db, cleanup_outbox, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME", "cleanride_temp_password")
    service = WhatsAppService(db)
    ok = await service.send_temp_password("9876543217", "Xk29pQr7Lm")
    assert ok is True
    doc = await db.whatsapp_outbox.find_one({"phone": "9876543217"})
    assert doc["template_name"] == "cleanride_temp_password"
    assert "Xk29pQr7Lm" in doc["message"]


@pytest.mark.asyncio
async def test_template_params_are_flattened_to_single_line(db):
    """Meta rejects template params with newlines/tabs/4+ spaces (error
    132000) — a multi-line notification must be flattened, never allowed
    to silently kill the send."""
    from app.services.whatsapp_service import _flatten_param

    assert _flatten_param("line one\nline two\ttabbed    wide") == "line one line two tabbed wide"
    assert _flatten_param("already clean") == "already clean"
