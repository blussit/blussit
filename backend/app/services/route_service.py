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

import httpx

from app.core.config import settings
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
        async with httpx.AsyncClient(timeout=6) as client:
            response = await client.post(
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
