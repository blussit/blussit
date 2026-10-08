"""The API host (api.blussit.com) must never be indexed: its payment-result
pages carry payment parameters in their URLs, and nothing else there is a
page anyone should land on from search."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.asyncio


async def test_every_api_response_says_noindex_and_robots_disallows_all(db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/api/health")
        assert health.headers["x-robots-tag"] == "noindex, nofollow"
        missing = await client.get("/api/v1/does-not-exist")
        assert missing.headers["x-robots-tag"] == "noindex, nofollow"
        robots = await client.get("/robots.txt")
        assert robots.status_code == 200 and "Disallow: /" in robots.text
