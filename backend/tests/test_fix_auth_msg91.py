"""
AUTH-02 + AUTH-03 regression tests — MSG91 widget tokens.

  - AUTH-02: MSG91's own verify response is the authority on WHICH number
    was verified. If it names an identifier and that isn't the expected
    phone, the token is refused — whatever the (unverified) JWT payload
    says. The payload is read only when the response names nothing.
  - AUTH-03: a widget token is single-use on our side — a second reset,
    or an OTP login, with an already-spent token is refused.
"""
import base64
import json

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.services.msg91_widget_service import Msg91WidgetService
from tests.factories import make_customer

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def fake_jwt(payload: dict) -> str:
    def part(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(payload)}.sig"


def stub(monkeypatch, body: dict):
    class R:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return body

    async def post(self, token):
        return R()

    monkeypatch.setattr(Msg91WidgetService, "_post_verify", post)
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", "test-auth-key")


@pytest.mark.parametrize("message", [
    "919111111111",                       # another phone
    {"mobile": "919111111111"},
    "attacker@example.com",               # an email
    "9876543210@attacker.example",        # an email spelling the victim's digits
    {"identifier": "attacker@example.com"},
])
async def test_response_identifier_that_differs_is_refused_whatever_the_payload_says(monkeypatch, message):
    stub(monkeypatch, {"type": "success", "message": message})
    token = fake_jwt({"identifier": "919876543210"})  # payload names the victim
    assert await Msg91WidgetService().verify_access_token(token, "9876543210") is False


@pytest.mark.parametrize("message", ["919876543210", "+91 98765 43210", {"mobile": "919876543210"}])
async def test_response_identifier_that_matches_passes(monkeypatch, message):
    stub(monkeypatch, {"type": "success", "message": message})
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": "911111111111"}), "9876543210") is True


async def test_payload_is_used_only_when_the_response_names_no_identifier(monkeypatch):
    stub(monkeypatch, {"type": "success", "message": "OTP verified successfully"})
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": "919876543210"}), "9876543210") is True
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": "919111111111"}), "9876543210") is False


@pytest.mark.parametrize("identifier", ["٩٨٧٦٥٤٣٢١٠", "９８７６５４３２１０", "91٩٨٧٦٥٤٣٢١٠"])
async def test_non_ascii_digits_never_bind(monkeypatch, identifier):
    stub(monkeypatch, {"type": "success", "message": "OTP verified"})
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": identifier}), "9876543210") is False
    stub(monkeypatch, {"type": "success", "message": identifier})
    assert await Msg91WidgetService().verify_access_token(fake_jwt({"identifier": "919876543210"}), "9876543210") is False


async def test_widget_token_is_single_use(db, cleanup, monkeypatch):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    stub(monkeypatch, {"type": "success", "message": f"91{phone}"})
    token = fake_jwt({"identifier": f"91{phone}", "n": "fix-auth-single-use"})
    async with _client() as c:
        r1 = await c.post("/api/v1/auth/reset-password/widget", json={"access_token": token, "phone": phone, "new_password": "First@123456"})
        r2 = await c.post("/api/v1/auth/reset-password/widget", json={"access_token": token, "phone": phone, "new_password": "Second@123456"})
        r3 = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "access_token": token})
        good = await c.post("/api/v1/auth/login", json={"identifier": phone, "password": "First@123456"})
        bad = await c.post("/api/v1/auth/login", json={"identifier": phone, "password": "Second@123456"})
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 400, r2.text
    assert r3.status_code == 400, r3.text
    assert good.status_code == 200 and bad.status_code == 401
    import hashlib

    spent = await db.used_widget_tokens.find_one({"_id": hashlib.sha256(token.encode()).hexdigest()})
    assert spent and spent.get("expires_at")
    await db.used_widget_tokens.delete_one({"_id": spent["_id"]})
