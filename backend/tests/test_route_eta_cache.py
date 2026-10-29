"""road_distance_eta must not pay Google twice for the same leg."""
from datetime import datetime, timezone

import pytest

from tests.test_offer_travel_prepaid import _FakeRoutes


@pytest.fixture
async def routes(db, monkeypatch):
    from app.core.config import settings
    from app.services import route_service

    fake = _FakeRoutes()
    seen = []
    real_post = fake.post

    async def post(url, headers=None, json=None):
        seen.append(json["routingPreference"])
        return await real_post(url, headers=headers, json=json)

    fake.post = post
    fake.seen = seen
    monkeypatch.setattr(settings, "GOOGLE_MAPS_SERVER_KEY", "test-key")
    monkeypatch.setattr(route_service, "shared_client", lambda name, timeout: fake)
    await db.road_distance_cache.delete_many({})
    yield fake
    await db.road_distance_cache.delete_many({})


async def test_store_leg_is_traffic_unaware_and_asked_once(db, routes):
    from app.services.route_service import road_distance_eta

    first = await road_distance_eta(22.68, 75.86, 22.74, 75.86, db=db)
    # A second car on the same visit, and a pin 20 m away, reuse the answer.
    again = await road_distance_eta(22.68, 75.86, 22.74, 75.86, db=db)
    nudged = await road_distance_eta(22.68, 75.86, 22.7401, 75.8601, db=db)
    assert first == again == nudged
    assert first["source"] == "google" and first["minutes"] == 15
    assert routes.seen == ["TRAFFIC_UNAWARE"], "one cheap call, not three"


async def test_live_leg_is_traffic_aware_and_kept_briefly(db, routes):
    from datetime import timedelta

    from app.services.route_service import road_distance_eta

    await road_distance_eta(22.70, 75.85, 22.74, 75.86, db=db, live=True)
    await road_distance_eta(22.70, 75.85, 22.74, 75.86, db=db, live=True)  # the drawer's 45 s poll
    assert routes.seen == ["TRAFFIC_AWARE"]
    doc = await db.road_distance_cache.find_one({"_id": {"$regex": "^eta:t:"}})
    ttl = doc["expires_at"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    assert ttl <= timedelta(minutes=2), "a moving captain's ETA must not go stale for long"


async def test_without_db_it_still_works_uncached(routes):
    from app.services.route_service import road_distance_eta

    await road_distance_eta(22.68, 75.86, 22.74, 75.86)
    await road_distance_eta(22.68, 75.86, 22.74, 75.86)
    assert len(routes.seen) == 2


async def test_google_down_falls_back_and_retries_soon(db, routes):
    from app.services.route_service import road_distance_eta

    routes.status, routes.meters = 500, None
    result = await road_distance_eta(22.68, 75.86, 22.74, 75.86, db=db)
    assert result["source"] == "haversine" and result["minutes"] is None
    doc = await db.road_distance_cache.find_one({"_id": {"$regex": "^eta:u:"}})
    ttl = doc["expires_at"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    assert ttl.total_seconds() <= 120
