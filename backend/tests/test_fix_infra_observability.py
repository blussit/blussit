"""DEP-09: request ids, structured logs, global masking of phone numbers and
secrets in every log record, and lease-claim database errors that are
logged instead of being mistaken for "someone else holds the lease"."""
import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from pymongo.errors import AutoReconnect, DuplicateKeyError

from app import main
from app.core import logging_setup
from app.core.logging_setup import JsonFormatter, redact, request_id_var

pytestmark = pytest.mark.asyncio


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")


# --------------------------------------------------------------- request id
async def test_incoming_request_id_is_echoed(db):
    async with _client() as c:
        r = await c.get("/api/health", headers={"X-Request-ID": "abc-123.DEF_456"})
    assert r.headers["x-request-id"] == "abc-123.DEF_456"


async def test_request_id_is_generated_when_absent_and_differs_per_request(db):
    async with _client() as c:
        a = await c.get("/api/health")
        b = await c.get("/api/health")
    assert len(a.headers["x-request-id"]) >= 16
    assert a.headers["x-request-id"] != b.headers["x-request-id"]


async def test_unsafe_incoming_request_id_is_replaced(db):
    async with _client() as c:
        r = await c.get("/api/health", headers={"X-Request-ID": "x" * 300})
        s = await c.get("/api/health", headers={"X-Request-ID": "evil value with spaces"})
    assert r.headers["x-request-id"] != "x" * 300 and len(r.headers["x-request-id"]) <= 128
    assert s.headers["x-request-id"] != "evil value with spaces"


async def test_cloud_trace_id_is_used_when_no_request_id(db):
    async with _client() as c:
        r = await c.get("/api/health", headers={"X-Cloud-Trace-Context": "105445aa7843bc8bf206b12000100000/1;o=1"})
    assert r.headers["x-request-id"] == "105445aa7843bc8bf206b12000100000"


async def test_request_id_reaches_log_records_and_error_responses_carry_it(db, monkeypatch, caplog):
    from app.controllers.catalog_controller import ServiceController

    async def boom(*_a, **_k):
        raise RuntimeError("exploded")

    monkeypatch.setattr(ServiceController, "list", boom)
    caplog.set_level(logging.ERROR)
    async with _client() as c:
        r = await c.get("/api/v1/services", headers={"X-Request-ID": "req-500-check", "Origin": "http://localhost:5173"})
    assert r.status_code == 500
    assert r.headers["x-request-id"] == "req-500-check"
    records = [rec for rec in caplog.records if "Unhandled exception" in rec.getMessage()]
    assert records and getattr(records[-1], "request_id", None) == "req-500-check"


def test_websockets_pass_through_the_request_id_middleware():
    """The outermost middleware must not break the WebSocket handshake: a
    bad token still gets the app's own 4401 close."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(main.app)
    with client.websocket_connect("/api/v1/ws?token=garbage", headers={"X-Request-ID": "ws-1"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


# -------------------------------------------------------------- redaction
@pytest.mark.parametrize(
    "raw, leaked",
    [
        ("WHATSAPP -> 9876543210: hi", "9876543210"),
        ("send to +919876543210 failed", "9876543210"),
        ("send to +91 9876543210 failed", "9876543210"),
        ("wa_id=919876543210", "9876543210"),
        ('{"phone": "6000000781"}', "6000000781"),
        ("Your BLUSSIT OTP is 482913", "482913"),
        ("verification code: 771144", "771144"),
        ("GET /api/v1/ws?token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig&x=1", "eyJhbGciOiJIUzI1NiJ9"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("password=Hunter2!x other", "Hunter2!x"),
        ('{"new_password": "Zq81LmPx"}', "Zq81LmPx"),
        ("client_secret=s3cr3tvalue", "s3cr3tvalue"),
        ("mongodb+srv://app:Sup3rS3cret@cluster0.example.net/db", "Sup3rS3cret"),
        ("hash $2b$12$abcdefghijklmnopqrstuuJ9C0vJ3xX5o9fY0q6c7Q6s1p2r3t4u5v", "$2b$12$abcdefghijklmnopqrstuu"),
    ],
)
def test_redact_masks_phones_and_secrets(raw, leaked):
    out = redact(raw)
    assert leaked not in out, out


@pytest.mark.parametrize(
    "safe",
    [
        "rate-limit client ip: peer=169.254.8.1 xff_entries=2 chosen=203.0.113.7",
        "Booking BK-20261007-0042 cancelled after 1791319078 seconds",
        "order_id=order_a2_000123 amount_paise=99900 status=500",
        "OTP sent (••••••)",
        "pay_AbC9876543210Xy",
    ],
)
def test_redact_leaves_ordinary_text_alone(safe):
    assert redact(safe) == safe


def test_phone_is_masked_in_a_captured_log_record(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("app.services.anything").info("Sending WhatsApp to %s about %s", "9876543210", {"phone": "+917000000001"})
    record = caplog.records[-1]
    message = record.getMessage()
    assert "9876543210" not in message and "7000000001" not in message
    assert "3210" in message, "the last four digits stay for support lookups"
    assert "9876543210" not in caplog.text


def test_exception_text_is_masked_too(caplog):
    caplog.set_level(logging.ERROR)
    try:
        raise ValueError("duplicate phone 9876543210 with password=hunter22")
    except ValueError:
        logging.getLogger("app.test").exception("insert failed")
    assert "9876543210" not in caplog.text and "hunter22" not in caplog.text


def test_json_formatter_emits_cloud_logging_fields():
    token = request_id_var.set("rid-json-1")
    try:
        record = logging.getLogger("app.json").makeRecord("app.json", logging.WARNING, __file__, 1, "call %s", ("9876543210",), None)
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)
    data = json.loads(line)
    assert data["severity"] == "WARNING"
    assert data["request_id"] == "rid-json-1"
    assert "9876543210" not in data["message"] and data["message"].startswith("call ")
    assert data["logger"] == "app.json"


def test_json_formatter_includes_the_traceback_in_the_message():
    try:
        raise RuntimeError("kaput")
    except RuntimeError:
        import sys

        record = logging.getLogger("app.json").makeRecord("app.json", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    data = json.loads(JsonFormatter().format(record))
    assert data["severity"] == "ERROR" and "Traceback" in data["message"] and "kaput" in data["message"]


def test_log_format_auto_is_json_in_production_only():
    assert logging_setup.wants_json("", "production") is True
    assert logging_setup.wants_json("", "development") is False
    assert logging_setup.wants_json("text", "production") is False
    assert logging_setup.wants_json("json", "development") is True


def test_sentry_hook_is_env_gated_and_needs_no_dependency(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    assert logging_setup.init_error_tracking("", "production") is False
    # DSN set but sentry-sdk isn't installed here: warns, never crashes.
    monkeypatch.setattr(logging_setup, "_import_sentry", lambda: None)
    assert logging_setup.init_error_tracking("https://key@o1.ingest.sentry.io/1", "production") is False
    assert any("sentry" in r.getMessage().lower() for r in caplog.records)


def test_sentry_event_scrubber_masks_phones_and_drops_request_secrets():
    event = {
        "message": "failed for 9876543210",
        "exception": {"values": [{"value": "password=hunter22"}]},
        "request": {"headers": {"Authorization": "Bearer x"}, "cookies": {"a": "b"}, "query_string": "token=abc", "data": {"otp": "123456"}},
    }
    out = logging_setup.scrub_event(event, None)
    text = json.dumps(out)
    for leaked in ("9876543210", "hunter22", "Bearer x", "token=abc", "123456"):
        assert leaked not in text


# ------------------------------------------------------------ lease claim
class _Locks:
    def __init__(self, insert_exc):
        self.insert_exc = insert_exc

    async def find_one_and_update(self, *_a, **_k):
        return None

    async def insert_one(self, *_a, **_k):
        raise self.insert_exc


class _Db:
    def __init__(self, insert_exc):
        self.locks = _Locks(insert_exc)


async def test_lease_claim_database_error_is_logged_not_swallowed(caplog):
    caplog.set_level(logging.WARNING)
    assert await main._claim_lease(_Db(AutoReconnect("primary stepped down")), "h1") is False
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "lease" in r.getMessage().lower()]
    assert errors, "a database failure must be logged, not read as 'someone else holds it'"


async def test_losing_the_lease_race_stays_quiet(caplog):
    caplog.set_level(logging.WARNING)
    assert await main._claim_lease(_Db(DuplicateKeyError("E11000 duplicate key")), "h1") is False
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
