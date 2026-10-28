"""Proxy trust hardening: 0 is never read as 1, 2+ needs an explicit opt-in."""
import pytest
from starlette.requests import Request

from app.core import rate_limit
from app.core.config import Settings, settings, validate_proxy_settings, validate_settings
from tests.test_fix_infra_config import GOOD_PRODUCTION

PEER = "169.254.8.1"


def _request(xff: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/v1/services", "headers": [(b"x-forwarded-for", xff.encode())],
        "client": (PEER, 40000), "query_string": b"", "server": ("test", 80), "scheme": "http",
    })


def test_count_zero_with_trust_on_trusts_nothing(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 0)
    assert rate_limit._client_ip(_request("203.0.113.7")) == PEER  # not silently treated as 1


@pytest.mark.parametrize(
    "trust, count, allow, expect_problem",
    [
        (True, 1, False, False),   # Cloud Run direct: the safe production value
        (True, 0, False, True),    # trust on but no hop: refuse to start
        (True, 2, False, True),    # two hops need the explicit opt-in
        (True, 2, True, False),
        (False, 0, False, False),  # not trusting: the count is irrelevant
        (False, 5, False, False),
    ],
)
def test_proxy_settings_rules(trust, count, allow, expect_problem):
    s = Settings(TRUST_PROXY_HEADERS=trust, TRUSTED_PROXY_COUNT=count, ALLOW_PROXY_COUNT_GT_1=allow)
    problems = validate_proxy_settings(s)
    assert bool(problems) is expect_problem
    if expect_problem:
        assert "TRUSTED_PROXY_COUNT" in problems[0]


def test_two_hops_in_production_needs_the_flag_and_the_boot_check_says_so():
    two = Settings(**{**GOOD_PRODUCTION, "TRUSTED_PROXY_COUNT": 2})
    assert any("ALLOW_PROXY_COUNT_GT_1" in p for p in validate_settings(two))
    allowed = Settings(**{**GOOD_PRODUCTION, "TRUSTED_PROXY_COUNT": 2, "ALLOW_PROXY_COUNT_GT_1": True})
    assert not any("TRUSTED_PROXY_COUNT" in p for p in validate_settings(allowed))
    assert not any("TRUSTED_PROXY_COUNT" in p for p in validate_settings(Settings(**GOOD_PRODUCTION)))
