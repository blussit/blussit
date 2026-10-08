"""
Road distance + travel time between the service center and the customer's
pin, via Google's Routes API — computed ONCE per booking (no live
tracking, per product decision), stored on the booking for the captain's
job card and the KPI travel stats.

Strictly best-effort: a missing key, missing coordinates, or a Google
hiccup degrades to the straight-line haversine estimate (marked as such)
and NEVER delays or blocks the booking itself.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.core.http_client import shared_client
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


async def road_distance_eta(origin_lat, origin_lng, dest_lat, dest_lng) -> dict | None:
    """Returns {"km", "minutes", "source"} or None when coordinates are
    missing entirely."""
    if None in (origin_lat, origin_lng, dest_lat, dest_lng):
        return None
    fallback = {
        "km": round(haversine_km(origin_lat, origin_lng, dest_lat, dest_lng), 1),
        "minutes": None,
        "source": "haversine",
    }
    if not settings.GOOGLE_MAPS_SERVER_KEY:
        return fallback
    try:
        response = await shared_client("google_routes", 6).post(
            ROUTES_URL,
            headers={
                "X-Goog-Api-Key": settings.GOOGLE_MAPS_SERVER_KEY,
                "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
                "Content-Type": "application/json",
            },
            json={
                "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}},
                "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lng}}},
                "travelMode": "TWO_WHEELER",
                "routingPreference": "TRAFFIC_AWARE",
            },
        )
        if response.status_code >= 300:
            logger.info("Routes API declined (%s): %s", response.status_code, response.text[:200])
            return fallback
        routes = response.json().get("routes") or []
        if not routes:
            return fallback
        meters = routes[0].get("distanceMeters")
        duration = routes[0].get("duration", "0s")  # e.g. "1234s"
        seconds = int(str(duration).rstrip("s") or 0)
        if not meters:
            return fallback
        return {"km": round(meters / 1000, 1), "minutes": max(round(seconds / 60), 1), "source": "google"}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("Routes API raised %s — falling back to haversine", exc)
        return fallback


# The customer distance charge (charges_travel services) is priced on this.
# Cached per center→pin pair so the quote the customer reads, the booking
# that charges it and the WhatsApp bot all see ONE number, and Google is
# asked once per address, not on every cart change. A Google answer is kept
# for 30 days; a fallback only 10 minutes, so a brief outage self-heals.
ROAD_KM_TTL = timedelta(days=30)
FALLBACK_KM_TTL = timedelta(minutes=10)


async def charge_road_km(db, origin_lat, origin_lng, dest_lat, dest_lng) -> dict | None:
    """Road distance (km, by two-wheeler — how our captains ride) from the
    center to the customer's pin: {"km", "source": "google" | "straight_line"}.
    Traffic-unaware on purpose — a price must not change with the time of
    day. Falls back to the straight-line distance (never more than the real
    road, so a hiccup can only undercharge). None when coordinates are
    missing."""
    if None in (origin_lat, origin_lng, dest_lat, dest_lng):
        return None
    # Both ends snapped to 3 decimals (~110 m): the public coverage check
    # takes any coordinates, and a 5-decimal key let every 1 m nudge of a
    # pin cost a fresh paid Google call. The charge is per km, so 110 m is
    # well inside its precision — and the distance is computed FROM the
    # snapped points, so every caller in one cell sees the same number.
    origin_lat, origin_lng, dest_lat, dest_lng = (round(float(v), 3) for v in (origin_lat, origin_lng, dest_lat, dest_lng))
    key = f"{origin_lat:.3f},{origin_lng:.3f}>{dest_lat:.3f},{dest_lng:.3f}"
    now = datetime.now(timezone.utc)
    cached = await db.road_distance_cache.find_one({"_id": key, "expires_at": {"$gt": now}})
    if cached:
        return {"km": cached["km"], "source": cached["source"]}

    km = await _google_road_km(origin_lat, origin_lng, dest_lat, dest_lng)
    result = (
        {"km": km, "source": "google"} if km is not None
        else {"km": round(haversine_km(origin_lat, origin_lng, dest_lat, dest_lng), 2), "source": "straight_line"}
    )
    ttl = ROAD_KM_TTL if km is not None else FALLBACK_KM_TTL
    await db.road_distance_cache.update_one({"_id": key}, {"$set": {**result, "expires_at": now + ttl}}, upsert=True)
    return result


async def _google_road_km(origin_lat, origin_lng, dest_lat, dest_lng) -> float | None:
    if not settings.GOOGLE_MAPS_SERVER_KEY:
        return None
    try:
        response = await shared_client("google_routes", 6).post(
            ROUTES_URL,
            headers={
                "X-Goog-Api-Key": settings.GOOGLE_MAPS_SERVER_KEY,
                "X-Goog-FieldMask": "routes.distanceMeters",
                "Content-Type": "application/json",
            },
            json={
                "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}},
                "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lng}}},
                "travelMode": "TWO_WHEELER",
                "routingPreference": "TRAFFIC_UNAWARE",
            },
        )
        if response.status_code >= 300:
            logger.info("Routes API declined road-km (%s): %s", response.status_code, response.text[:200])
            return None
        routes = response.json().get("routes") or []
        meters = routes[0].get("distanceMeters") if routes else None
        return round(meters / 1000, 2) if meters else None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("Routes API raised %s for road-km — using straight line", exc)
        return None
