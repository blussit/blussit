"""
Service zones — coverage by drawn polygon, not pincode (AUDIT/geo plan
item 4). The admin draws each center's real coverage area on a map; a
booking's pinned lat/lng is checked against the polygons with MongoDB's
native $geoIntersects (2dsphere index — zero external API cost).

Rollout rule, stated plainly: the zone check only takes over WHERE IT CAN.
  - Zones exist AND the point has coordinates  -> the polygon decides.
  - No zones drawn yet (or a legacy address with no pin) -> the existing
    pincode/nearest-center behavior stands, so nothing breaks the day
    this ships with zero zones drawn.
Pincode becomes display/fallback the moment real zones exist.
"""
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import WriteError

from app.core.exceptions import BadRequestException, NotFoundException
from app.utils.serializers import serialize_doc, serialize_list

_CROSSING_MSG = "The zone outline crosses itself — click the corners going around the boundary in order, then finish."


def _segments_cross(a, b, c, d) -> bool:
    """Proper crossing of segment ab with segment cd (touching at a shared
    corner doesn't count — adjacent edges are excluded by the caller)."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def _validate_ring(ring: list) -> list:
    if not isinstance(ring, list) or len(ring) < 4:
        raise BadRequestException("A zone needs at least 3 corners")
    cleaned = []
    for point in ring:
        if (not isinstance(point, (list, tuple))) or len(point) != 2:
            raise BadRequestException("Zone corners must be [longitude, latitude] pairs")
        lng, lat = float(point[0]), float(point[1])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise BadRequestException("Zone corner out of range")
        if not cleaned or [lng, lat] != cleaned[-1]:  # drop double-clicked corners
            cleaned.append([lng, lat])
    if cleaned[0] != cleaned[-1]:
        cleaned.append(cleaned[0])  # GeoJSON rings must close
    if len(cleaned) < 4:
        raise BadRequestException("A zone needs at least 3 distinct corners")
    # MongoDB rejects a self-intersecting ring with an opaque 500 ("Loop is
    # not valid") — catch it here and tell the admin what to actually fix.
    edges = [(cleaned[i], cleaned[i + 1]) for i in range(len(cleaned) - 1)]
    n = len(edges)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # first and last edge legitimately share the closing corner
            if _segments_cross(*edges[i], *edges[j]):
                raise BadRequestException(_CROSSING_MSG)
    return cleaned


class ZoneService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def list_zones(self, service_center_id: str | None = None) -> list[dict]:
        query: dict = {"is_deleted": {"$ne": True}}
        if service_center_id:
            query["service_center_id"] = service_center_id
        return serialize_list(await self.db.service_zones.find(query).sort("name", 1).to_list(length=None))

    async def create_zone(self, name: str, service_center_id: str, ring: list) -> dict:
        center = await self.db.service_centers.find_one({"_id": ObjectId(service_center_id)}) if ObjectId.is_valid(service_center_id) else None
        if not center:
            raise NotFoundException("Service center not found")
        doc = {
            "name": name.strip()[:80] or "Zone",
            "service_center_id": service_center_id,
            "polygon": {"type": "Polygon", "coordinates": [_validate_ring(ring)]},
            "is_active": True,
            "is_deleted": False,
        }
        try:
            result = await self.db.service_zones.insert_one(doc)
        except WriteError:
            raise BadRequestException(_CROSSING_MSG)
        doc["_id"] = result.inserted_id
        return serialize_doc(doc)

    async def update_zone(self, zone_id: str, *, name: str | None = None, ring: list | None = None, is_active: bool | None = None) -> dict:
        update: dict = {}
        if name is not None:
            update["name"] = name.strip()[:80]
        if ring is not None:
            update["polygon"] = {"type": "Polygon", "coordinates": [_validate_ring(ring)]}
        if is_active is not None:
            update["is_active"] = is_active
        if not update:
            raise BadRequestException("Nothing to update")
        try:
            result = await self.db.service_zones.find_one_and_update(
                {"_id": ObjectId(zone_id)}, {"$set": update}, return_document=True
            )
        except WriteError:
            raise BadRequestException(_CROSSING_MSG)
        if not result:
            raise NotFoundException("Zone not found")
        return serialize_doc(result)

    async def delete_zone(self, zone_id: str) -> None:
        result = await self.db.service_zones.update_one({"_id": ObjectId(zone_id)}, {"$set": {"is_deleted": True, "is_active": False}})
        if not result.matched_count:
            raise NotFoundException("Zone not found")

    # ------------------------------------------------------------------

    async def zones_exist(self) -> bool:
        return await self.db.service_zones.count_documents({"is_active": True, "is_deleted": {"$ne": True}}, limit=1) > 0

    async def zones_for_point(self, latitude: float, longitude: float) -> list[dict]:
        """Every active zone whose polygon contains the point — pure
        MongoDB geo math, no external API."""
        return await self.db.service_zones.find({
            "is_active": True,
            "is_deleted": {"$ne": True},
            "polygon": {"$geoIntersects": {"$geometry": {"type": "Point", "coordinates": [longitude, latitude]}}},
        }).to_list(length=None)
