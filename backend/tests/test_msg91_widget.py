"""
MSG91 OTP-widget verification — the server-side trust boundary. A widget
token counts ONLY when (a) MSG91 confirms it and (b) it is bound to the
exact phone being verified. Anything else fails closed.
"""
import base64
import json

import pytest

from app.core.config import settings
from app.services import msg91_widget_service as mod
from app.services.msg91_widget_service import Msg91WidgetService


def fake_jwt(payload: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(payload)}.signature"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


def patch_msg91(monkeypatch, status=200, body=None):
    body = body if body is not None else {"type": "success", "message": "OTP verified"}

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            return FakeResponse(status, body)

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", "test-auth-key")


@pytest.mark.asyncio
async def test_valid_token_bound_to_expected_phone_passes(monkeypatch):
    patch_msg91(monkeypatch)
    token = fake_jwt({"identifier": "919876543210"})
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is True


@pytest.mark.asyncio
async def test_valid_token_for_a_DIFFERENT_phone_is_rejected(monkeypatch):
    # The attack this design exists to stop: verify your own number, then
    # replay the token against someone else's account.
    patch_msg91(monkeypatch)
    token = fake_jwt({"identifier": "911111111111"})
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is False


@pytest.mark.asyncio
async def test_token_with_no_identifier_anywhere_fails_closed(monkeypatch):
    patch_msg91(monkeypatch, body={"type": "success"})
    token = fake_jwt({"foo": "bar"})
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is False


@pytest.mark.asyncio
async def test_msg91_rejection_is_rejection(monkeypatch):
    patch_msg91(monkeypatch, body={"type": "error", "message": "invalid token"})
    token = fake_jwt({"identifier": "919876543210"})
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is False


@pytest.mark.asyncio
async def test_identifier_in_response_message_also_binds(monkeypatch):
    patch_msg91(monkeypatch, body={"type": "success", "message": {"mobile": "919876543210"}})
    token = fake_jwt({})
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is True


@pytest.mark.asyncio
async def test_disabled_without_auth_key(monkeypatch):
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", "")
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": "919876543210"}), "9876543210") is False
