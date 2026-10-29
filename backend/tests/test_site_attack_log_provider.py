"""Site-attack QA 2026-10-29: the log WhatsApp provider has no Meta account, so
template creation must fail cleanly (BadRequest -> 400), never AttributeError (500)."""
import pytest

from app.core.exceptions import BadRequestException
from app.services.whatsapp_crm_service import WhatsAppCrmService
from app.services.whatsapp_service import LogWhatsAppProvider


async def test_log_provider_create_template_returns_error_dict(db):
    result = await LogWhatsAppProvider(db).create_template({"name": "x"})
    assert "log-only" in result["error"]


async def test_crm_create_template_with_log_provider_is_a_bad_request(db):
    service = WhatsAppCrmService(db)
    service.wa.provider = LogWhatsAppProvider(db)
    with pytest.raises(BadRequestException):
        await service.create_template("qa_template", "UTILITY", "en_US", "Hello", None, None)
