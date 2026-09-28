"""
Rate-limit key behind proxies (Cloud Run): with TRUST_PROXY_HEADERS the
client is the X-Forwarded-For entry at -TRUSTED_PROXY_COUNT (what the
nearest trusted proxy appended); client-supplied entries to its left are
ignored; a missing/short/garbage header falls back to the socket peer.
"""
import logging

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core import rate_limit
from app.core.config import settings

PEER = "169.254.8.1"


def _request(*xff: str, path: str = "/api/v1/services", method: str = "GET") -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in xff]
    return Request({
        "type": "http", "method": method, "path": path, "headers": headers,
        "client": (PEER, 40000), "query_string": b"", "server": ("test", 80), "scheme": "http",
    })


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    return monkeypatch


def test_untrusted_mode_uses_the_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    assert rate_limit._client_ip(_request("203.0.113.7")) == PEER
    assert rate_limit._client_ip(_request()) == PEER


def test_one_hop_picks_the_entry_the_proxy_appended(trusted):
    assert rate_limit._client_ip(_request("203.0.113.7")) == "203.0.113.7"


def test_spoofed_left_entries_are_ignored(trusted):
    assert rate_limit._client_ip(_request("6.6.6.6, 7.7.7.7, 203.0.113.7")) == "203.0.113.7"
    # A client-sent header and the proxy's own can arrive as separate lines.
    assert rate_limit._client_ip(_request("6.6.6.6", "203.0.113.7")) == "203.0.113.7"


def test_two_hops_skip_the_load_balancer_entry(trusted):
    trusted.setattr(settings, "TRUSTED_PROXY_COUNT", 2)
    assert rate_limit._client_ip(_request("6.6.6.6, 203.0.113.7, 35.191.10.20")) == "203.0.113.7"
    # Too short for two trusted hops: nothing trustworthy in it.
    assert rate_limit._client_ip(_request("203.0.113.7")) == PEER


def test_garbage_or_missing_header_falls_back_to_the_peer(trusted):
    assert rate_limit._client_ip(_request()) == PEER
    assert rate_limit._client_ip(_request("")) == PEER
    assert rate_limit._client_ip(_request(" , ")) == PEER
    assert rate_limit._client_ip(_request("203.0.113.7, not-an-ip")) == PEER
    assert rate_limit._client_ip(_request("999.1.1.1")) == PEER


def test_port_and_ipv6_forms_are_normalized(trusted):
    assert rate_limit._client_ip(_request("203.0.113.7:5678")) == "203.0.113.7"
    assert rate_limit._client_ip(_request("[2001:db8::1]:443")) == "2001:db8::1"
    assert rate_limit._client_ip(_request("2001:DB8:0::1")) == "2001:db8::1"


def test_choice_is_logged_once_per_process(trusted, caplog):
    trusted.setattr(rate_limit, "_proxy_choice_logged", False)
    with caplog.at_level(logging.WARNING, logger="app.core.rate_limit"):
        rate_limit._client_ip(_request())  # no header: nothing worth logging yet
        rate_limit._client_ip(_request("6.6.6.6, 203.0.113.7"))
        rate_limit._client_ip(_request("198.51.100.1"))
    lines = [r.getMessage() for r in caplog.records if "rate-limit client ip" in r.getMessage()]
    assert lines == [f"rate-limit client ip: peer={PEER} xff_entries=2 chosen=203.0.113.7 trust_proxy_headers=True trusted_proxy_count=1"]


@pytest.mark.asyncio
async def test_customers_behind_one_proxy_get_separate_buckets(trusted):
    trusted.setattr(settings, "RATE_LIMIT_ENABLED", True)
    trusted.setattr(rate_limit, "_WINDOWS", rate_limit.defaultdict(int))

    async def ok(_request):
        return JSONResponse({"ok": True})

    async def send(ip: str) -> int:
        response = await rate_limit.rate_limit_middleware(_request(ip, path="/api/v1/auth/otp/request", method="POST"), ok)
        return response.status_code

    limit = next(r[1] for r in rate_limit.RULES if r[0] == "/api/v1/auth/otp/request")
    assert [await send("203.0.113.7") for _ in range(limit)] == [200] * limit
    assert await send("203.0.113.7") == 429
    # Same peer (the proxy), different customer: its own bucket.
    assert await send("198.51.100.1") == 200
