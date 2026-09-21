"""
Speed work on the public/booking read path:
  - the slot picker's availability read looks the capacity policy up ONCE per
    day, not once per slot (it used to be ~2 database round trips x ~10 slots);
  - anonymous catalogue GETs are cacheable for a short while and gzip'd, while
    anything carrying a login is never given a cache header.
"""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.booking_service import BookingService
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import make_service_center


@pytest.mark.asyncio
async def test_availability_reads_the_capacity_policy_once_per_day(db, cleanup, monkeypatch):
    center_id = await make_service_center(db, pincode="452066")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    service = BookingService(db)
    calls = []
    real = service.capacity_policy_service.get_effective_policy

    async def counting(center, date_str):
        calls.append((center, date_str))
        return await real(center, date_str)

    monkeypatch.setattr(service.capacity_policy_service, "get_effective_policy", counting)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    slots = await service.available_slots(center_id, tomorrow)
    assert len(slots) > 3, "the test center should have several slots"
    assert len(calls) == 1, f"policy looked up {len(calls)} times for {len(slots)} slots"
    assert all(s["status"] in {"available", "low", "full"} for s in slots)


@pytest.mark.asyncio
async def test_anonymous_catalogue_reads_are_cacheable_and_logged_in_ones_are_not(db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = await client.get("/api/v1/services", params={"page_size": 100}, headers={"Accept-Encoding": "gzip"})
        assert anon.status_code == 200
        assert "max-age=" in anon.headers.get("cache-control", "") and "public" in anon.headers["cache-control"]
        assert "authorization" in anon.headers.get("vary", "").lower()

        # A request that carries a login must never be told it may be cached.
        authed = await client.get("/api/v1/services", params={"page_size": 100}, headers={"Authorization": "Bearer not-a-real-token"})
        assert "cache-control" not in authed.headers or "public" not in authed.headers["cache-control"]

        # Only the catalogue whitelist is cacheable — never user/booking data or writes.
        for path in ("/api/v1/bookings", "/api/v1/auth/me", "/api/v1/notifications"):
            other = await client.get(path)
            assert "public" not in other.headers.get("cache-control", "")
        posted = await client.post("/api/v1/auth/login", json={"identifier": "x@example.com", "password": "nope"})
        assert "public" not in posted.headers.get("cache-control", "")
