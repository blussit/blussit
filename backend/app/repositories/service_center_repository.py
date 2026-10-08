import random
import string

from app.repositories.base_repository import BaseRepository, build_search_filter
from app.utils.geo import haversine_km


# A center is active unless switched off on purpose: centers created before
# `is_active` was stored on create (QA 2026-10-07) have no field at all.
ACTIVE_CENTER = {"is_active": {"$ne": False}}


class ServiceCenterRepository(BaseRepository):
    collection_name = "service_centers"

    async def search(self, search: str | None, page: int, page_size: int, active_only: bool = False):
        filters: dict = dict(ACTIVE_CENTER) if active_only else {}
        if search:
            filters.update(build_search_filter(search, ["name", "code", "location.city", "location.pincode"]))
        return await self.find_many(filters, page=page, page_size=page_size)

    async def find_by_pincode(self, pincode: str) -> list[dict]:
        return await self.find_all_no_paginate({"location.service_pincodes": pincode, **ACTIVE_CENTER})

    async def find_nearest(self, latitude: float, longitude: float) -> tuple[dict, float] | None:
        """
        Returns the nearest active service center that actually covers this
        point (distance <= that center's configured radius_km), plus the
        distance in km. Pure haversine math — no external geocoding API
        required. At current expected scale (tens to low hundreds of
        centers) an in-memory scan is simpler and fast enough; this can be
        swapped for a MongoDB $geoNear query later without touching callers.
        """
        centers = await self.find_all_no_paginate(dict(ACTIVE_CENTER))
        candidates: list[tuple[dict, float]] = []
        for center in centers:
            loc = center.get("location", {})
            lat, lng = loc.get("latitude"), loc.get("longitude")
            if lat is None or lng is None:
                continue
            distance = haversine_km(latitude, longitude, lat, lng)
            radius = loc.get("radius_km", 6.0)
            if distance <= radius:
                candidates.append((center, distance))

        if not candidates:
            return None
        candidates.sort(key=lambda c: c[1])
        return candidates[0]

    @staticmethod
    def generate_code(city: str) -> str:
        prefix = "".join(ch for ch in city.upper() if ch.isalpha())[:3] or "SVC"
        suffix = "".join(random.choices(string.digits, k=4))
        return f"{prefix}-{suffix}"
