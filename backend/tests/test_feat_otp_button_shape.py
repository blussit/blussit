"""The OTP (authentication) template's code button is sent as a URL-type
component with the code as text — Meta refuses the old copy_code shape
(132018), which silently pushed every login code to SMS."""
import pytest

from app.services.whatsapp_service import MetaCloudWhatsAppProvider

pytestmark = pytest.mark.asyncio


async def test_otp_button_is_a_url_component_with_the_code(monkeypatch):
    seen = {}
    provider = MetaCloudWhatsAppProvider.__new__(MetaCloudWhatsAppProvider)

    async def fake_post(self, phone, text, body, template_name=None, extra=None):
        seen["body"] = body
        from app.services.whatsapp_service import SendOutcome
        return SendOutcome(True)

    monkeypatch.setattr(MetaCloudWhatsAppProvider, "_post_outcome", fake_post)
    await provider.send_template("9876500001", "blussit_otp", "en_US", ["482915"], otp_button=True, extra={"kind": "otp"})
    comps = seen["body"]["template"]["components"]
    button = next(c for c in comps if c["type"] == "button")
    assert button == {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "482915"}]}
    assert next(c for c in comps if c["type"] == "body")["parameters"] == [{"type": "text", "text": "482915"}]
