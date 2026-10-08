"""FAIL-03 — an R2 outage is a 503 "storage unavailable, retry" (not a 500
or a "bad photo" 400), so the captain app retries instead of giving up."""
import httpx
import pytest

from app.core import storage
from app.core.exceptions import BadRequestException, StorageUnavailableException

pytestmark = pytest.mark.asyncio


class _Client:
    def __init__(self, outcome):
        self.outcome = outcome

    async def put(self, *_a, **_k):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return httpx.Response(self.outcome)

    get = put


@pytest.fixture
def r2(monkeypatch):
    monkeypatch.setattr(storage, "_r2_config", lambda bucket=None: ("https://acct.r2.cloudflarestorage.com", "bucket", "https://cdn.test", "ak", "sk"))

    def use(outcome):
        monkeypatch.setattr(storage, "_r2_http", lambda: _Client(outcome))

    return use


@pytest.mark.parametrize("outcome", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), 503, 500, 429])
async def test_r2_outage_is_a_retryable_503(r2, outcome):
    r2(outcome)
    with pytest.raises(StorageUnavailableException) as err:
        await storage._r2_upload(b"x", "jpg", "image/jpeg")
    assert err.value.status_code == 503
    with pytest.raises(StorageUnavailableException):
        await storage.download_private_document("docs/a.jpg")


async def test_r2_rejecting_the_request_stays_a_400(r2):
    r2(403)
    with pytest.raises(BadRequestException):
        await storage._r2_upload(b"x", "jpg", "image/jpeg")


async def test_r2_success_returns_the_public_url(r2):
    r2(200)
    url = await storage._r2_upload(b"x", "jpg", "image/jpeg", prefix="photos")
    assert url.startswith("https://cdn.test/photos/")
