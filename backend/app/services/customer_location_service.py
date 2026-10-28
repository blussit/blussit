"""Where each CUSTOMER is — stored flat on the users doc so staff can filter
and sort by center, city and pincode without joining addresses/bookings.

Fields (customers only; staff-only in API responses):
  home_center_id        service center that covers the customer's home
  home_city / home_state / home_pincode / home_lat / home_lng (nullable)
  location_source       "address" | "booking" | "manual"
  location_updated_at   last time this block was (re)derived (also the
                        "checked" stamp the backfill resumes on)
  first_booking_center_id / last_booking_center_id / last_booking_at
  bookings_count        booking rows ever created (cancelled included)

ONE source of truth per field, precedence for the home block:
  1. the DEFAULT saved address (else the newest one) -> the center that
     covers it (zones when drawn, else nearest-within-radius, else pincode);
  2. else the most recent booking's center (+ that booking's address
     snapshot for city/pincode/pin);
  3. else nothing.
"manual" (an admin set it) is never overwritten by 1/2; only counters move.
Everything here is non-throwing for callers: hooks swallow and log.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

LOCATION_FIELDS = (
    "home_center_id", "home_city", "home_state", "home_pincode", "home_lat", "home_lng",
    "location_source", "location_updated_at",
    "first_booking_center_id", "last_booking_center_id", "last_booking_at", "bookings_count",
)
# Exact coordinates are admin-only; managers get the rest.
COORDINATE_FIELDS = ("home_lat", "home_lng")
_BATCH = 500


class CenterResolver:
    """Which active center covers an address — non-throwing, batch friendly
    (centers and the zones flag are loaded once)."""

    def __init__(self, db: AsyncIOMotorDatabase, centers: list[dict], zoned: bool):
        self.db, self.centers, self.zoned = db, centers, zoned
        self._by_id = {str(c["_id"]): c for c in centers}

    @classmethod
    async def load(cls, db: AsyncIOMotorDatabase) -> "CenterResolver":
        from app.services.zone_service import ZoneService

        centers = await db.service_centers.find({"is_active": {"$ne": False}, "is_deleted": {"$ne": True}}).to_list(length=None)
        return cls(db, centers, await ZoneService(db).zones_exist())

    def center(self, center_id: Optional[str]) -> Optional[dict]:
        return self._by_id.get(str(center_id)) if center_id else None

    async def resolve(self, lat, lng, pincode) -> Optional[str]:
        try:
            has_pin = lat is not None and lng is not None
            if has_pin and self.zoned:
                from app.services.zone_service import ZoneService

                best = None
                for zone in await ZoneService(self.db).zones_for_point(float(lat), float(lng)):
                    center = self._by_id.get(str(zone.get("service_center_id")))
                    if not center:
                        continue
                    loc = center.get("location") or {}
                    d = haversine_km(lat, lng, loc["latitude"], loc["longitude"]) if loc.get("latitude") is not None and loc.get("longitude") is not None else 0.0
                    if best is None or d < best[1]:
                        best = (str(center["_id"]), d)
                return best[0] if best else None
            if has_pin:
                best = None
                for center in self.centers:
                    loc = center.get("location") or {}
                    if loc.get("latitude") is None or loc.get("longitude") is None:
                        continue
                    d = haversine_km(lat, lng, loc["latitude"], loc["longitude"])
                    if d <= (loc.get("radius_km") or 6.0) and (best is None or d < best[1]):
                        best = (str(center["_id"]), d)
                if best:
                    return best[0]
            pin = str(pincode or "").strip()
            if pin:
                for center in self.centers:
                    if pin in ((center.get("location") or {}).get("service_pincodes") or []):
                        return str(center["_id"])
        except Exception:  # noqa: BLE001 — a bad coordinate never breaks a save
            logger.exception("customer location: center lookup failed")
        return None


def _num(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _address_block(address: dict) -> dict:
    return {
        "city": (address.get("city") or "").strip() or None,
        "state": (address.get("state") or "").strip() or None,
        "pincode": (address.get("pincode") or "").strip() or None,
        "lat": _num(address.get("latitude")),
        "lng": _num(address.get("longitude")),
    }


async def _home_block(resolver: CenterResolver, addresses: list[dict], last_booking: Optional[dict]) -> dict:
    """The home_* block by the documented precedence ({} = nothing known)."""
    if addresses:
        chosen = next((a for a in addresses if a.get("is_default")), None) or addresses[0]
        a = _address_block(chosen)
        center_id = await resolver.resolve(a["lat"], a["lng"], a["pincode"])
        if center_id:
            return {"home_center_id": center_id, "home_city": a["city"], "home_state": a["state"], "home_pincode": a["pincode"],
                    "home_lat": a["lat"], "home_lng": a["lng"], "location_source": "address"}
    if last_booking and last_booking.get("service_center_id"):
        snap = _address_block(last_booking.get("address_snapshot") or {})
        loc = (resolver.center(last_booking["service_center_id"]) or {}).get("location") or {}
        return {"home_center_id": last_booking["service_center_id"],
                "home_city": snap["city"] or loc.get("city"), "home_state": snap["state"] or loc.get("state"),
                "home_pincode": snap["pincode"] or loc.get("pincode"), "home_lat": snap["lat"], "home_lng": snap["lng"],
                "location_source": "booking"}
    if addresses:  # a saved address nobody covers: still useful to sort by
        chosen = next((a for a in addresses if a.get("is_default")), None) or addresses[0]
        a = _address_block(chosen)
        return {"home_center_id": None, "home_city": a["city"], "home_state": a["state"], "home_pincode": a["pincode"],
                "home_lat": a["lat"], "home_lng": a["lng"], "location_source": "address"}
    return {}


_EMPTY_HOME = {
    "home_center_id": None, "home_city": None, "home_state": None, "home_pincode": None,
    "home_lat": None, "home_lng": None, "location_source": None,
}


def _counters(stats: Optional[dict]) -> dict:
    stats = stats or {}
    return {
        "bookings_count": int(stats.get("count") or 0),
        "first_booking_center_id": stats.get("first_center"),
        "last_booking_center_id": stats.get("last_center"),
        "last_booking_at": stats.get("last_at"),
    }


async def _booking_stats(db, customer_ids: list[str]) -> dict[str, dict]:
    rows = await db.bookings.aggregate([
        {"$match": {"customer_id": {"$in": customer_ids}, "is_deleted": {"$ne": True}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$customer_id", "count": {"$sum": 1}, "first_center": {"$first": "$service_center_id"},
                    "last_center": {"$last": "$service_center_id"}, "last_at": {"$last": "$created_at"},
                    "last_booking": {"$last": {"service_center_id": "$service_center_id", "address_snapshot": "$address_snapshot"}}}},
    ], allowDiskUse=True).to_list(length=None)
    return {r["_id"]: r for r in rows}


async def _addresses_by_owner(db, owner_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    async for a in db.addresses.find({"owner_id": {"$in": owner_ids}, "is_deleted": {"$ne": True}}).sort("created_at", -1):
        out.setdefault(a["owner_id"], []).append(a)
    return out


async def derive_for(db, users: list[dict], resolver: Optional[CenterResolver] = None) -> list[tuple[ObjectId, dict]]:
    """(user _id, $set doc) for each customer — pure derivation, no writes."""
    resolver = resolver or await CenterResolver.load(db)
    ids = [str(u["_id"]) for u in users]
    stats = await _booking_stats(db, ids)
    addresses = await _addresses_by_owner(db, ids)
    now = datetime.now(timezone.utc)
    out = []
    for u in users:
        uid = str(u["_id"])
        s = stats.get(uid)
        data = _counters(s)
        if u.get("location_source") != "manual":
            data.update(_EMPTY_HOME)
            data.update(await _home_block(resolver, addresses.get(uid) or [], (s or {}).get("last_booking")))
        data["location_updated_at"] = now
        out.append((u["_id"], data))
    return out


async def refresh_customer_location(db: AsyncIOMotorDatabase, customer_id: str) -> Optional[dict]:
    """Re-derive one customer's block. Non-throwing; staff accounts ignored."""
    try:
        if not ObjectId.is_valid(str(customer_id)):
            return None
        user = await db.users.find_one({"_id": ObjectId(str(customer_id)), "role": "customer"}, {"role": 1, "location_source": 1})
        if not user:
            return None
        (_, data), = await derive_for(db, [user])
        await db.users.update_one({"_id": user["_id"]}, {"$set": data})
        return data
    except Exception:  # noqa: BLE001
        logger.exception("customer location refresh failed for %s", customer_id)
        return None


async def note_booking_created(db: AsyncIOMotorDatabase, booking: dict) -> None:
    """Hook for booking creation (one cheap write; never raises)."""
    try:
        customer_id, center_id = booking.get("customer_id"), booking.get("service_center_id")
        if not customer_id or not ObjectId.is_valid(str(customer_id)):
            return
        user = await db.users.find_one(
            {"_id": ObjectId(str(customer_id)), "role": "customer"},
            {"bookings_count": 1, "home_center_id": 1, "location_source": 1, "first_booking_center_id": 1},
        )
        if not user:
            return
        if "bookings_count" not in user:  # never derived yet: do it properly, once
            await refresh_customer_location(db, str(customer_id))
            return
        when = booking.get("created_at") or datetime.now(timezone.utc)
        sets: dict = {"last_booking_center_id": center_id, "last_booking_at": when}
        if not user.get("first_booking_center_id"):
            sets["first_booking_center_id"] = center_id
        if not user.get("home_center_id") and user.get("location_source") != "manual" and center_id:
            snap = _address_block(booking.get("address_snapshot") or {})
            sets.update({"home_center_id": center_id, "home_city": snap["city"], "home_state": snap["state"],
                         "home_pincode": snap["pincode"], "home_lat": snap["lat"], "home_lng": snap["lng"],
                         "location_source": "booking", "location_updated_at": datetime.now(timezone.utc)})
        await db.users.update_one({"_id": user["_id"]}, {"$set": sets, "$inc": {"bookings_count": 1}})
    except Exception:  # noqa: BLE001
        logger.exception("customer location: booking hook failed")


async def set_manual_location(db: AsyncIOMotorDatabase, customer_id: str, center_id: Optional[str]) -> dict:
    """Admin override: pin the home center by hand ("manual"); None clears the
    override and re-derives."""
    from app.core.exceptions import NotFoundException

    user = await db.users.find_one({"_id": ObjectId(customer_id), "role": "customer"}) if ObjectId.is_valid(customer_id) else None
    if not user:
        raise NotFoundException("Customer not found")
    if center_id:
        center = await db.service_centers.find_one({"_id": ObjectId(center_id), "is_deleted": {"$ne": True}}) if ObjectId.is_valid(center_id) else None
        if not center:
            raise NotFoundException("Service center not found")
        loc = center.get("location") or {}
        await db.users.update_one({"_id": user["_id"]}, {"$set": {
            "home_center_id": center_id, "home_city": loc.get("city"), "home_state": loc.get("state"),
            "home_pincode": loc.get("pincode"), "home_lat": None, "home_lng": None,
            "location_source": "manual", "location_updated_at": datetime.now(timezone.utc)}})
    else:
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"location_source": None}})
        await refresh_customer_location(db, customer_id)
    return location_view(await db.users.find_one({"_id": user["_id"]}), include_coordinates=True)


# -- Response shaping (staff only) -----------------------------------------


def location_view(user: Optional[dict], *, include_coordinates: bool) -> dict:
    """The location block for a staff response. Coordinates: admin only."""
    user = user or {}
    out = {
        "home_center_id": user.get("home_center_id"), "home_city": user.get("home_city"), "home_state": user.get("home_state"),
        "home_pincode": user.get("home_pincode"), "location_source": user.get("location_source"),
        "first_booking_center_id": user.get("first_booking_center_id"), "last_booking_center_id": user.get("last_booking_center_id"),
        "bookings_count": user.get("bookings_count"),
    }
    from app.utils.timezone import from_stored

    for key in ("location_updated_at", "last_booking_at"):
        value = user.get(key)
        out[key] = from_stored(value).isoformat() if isinstance(value, datetime) else value
    if include_coordinates:
        out["home_lat"], out["home_lng"] = user.get("home_lat"), user.get("home_lng")
    return out


async def attach_center_names(db, rows: list[dict]) -> None:
    """Add home_center_name (resolved by id — no name snapshot is stored)."""
    ids = {r.get("home_center_id") for r in rows if r.get("home_center_id")}
    names = {}
    if ids:
        names = {str(c["_id"]): c.get("name") for c in await db.service_centers.find(
            {"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}}, {"name": 1}).to_list(length=None)}
    for r in rows:
        r["home_center_name"] = names.get(r.get("home_center_id")) if r.get("home_center_id") else None


# -- Backfill (boot task + script) -----------------------------------------


async def backfill_customer_locations(
    db: AsyncIOMotorDatabase, *, budget_seconds: float = 20.0, batch_size: int = _BATCH, all_customers: bool = False,
    after_id: Optional[str] = None,
) -> dict:
    """Derive the location block for customers in bounded batches.

    Default (boot) mode takes customers with no `location_updated_at`, so it
    resumes by itself and is a no-op once everyone is stamped. `all_customers`
    (the script's --all) walks every customer by _id from `after_id` and
    returns `last_id` to resume with. Stops when the time budget is spent."""
    started = time.monotonic()
    resolver = await CenterResolver.load(db)
    processed = 0
    last_id = ObjectId(after_id) if after_id else None
    while True:  # always one batch per pass, then stop once the budget is spent
        query: dict = {"role": "customer"}
        if all_customers:
            if last_id:
                query["_id"] = {"$gt": last_id}
        else:
            query["location_updated_at"] = {"$exists": False}
            if last_id:
                query["_id"] = {"$gt": last_id}
        users = await db.users.find(query, {"role": 1, "location_source": 1}).sort("_id", 1).limit(batch_size).to_list(length=batch_size)
        if not users:
            return {"processed": processed, "done": True, "last_id": str(last_id) if last_id else None}
        from pymongo import UpdateOne

        ops = [UpdateOne({"_id": uid}, {"$set": data}) for uid, data in await derive_for(db, users, resolver)]
        await db.users.bulk_write(ops, ordered=False)
        processed += len(users)
        last_id = users[-1]["_id"]
        if time.monotonic() - started >= budget_seconds:
            break
    return {"processed": processed, "done": False, "last_id": str(last_id) if last_id else None}


async def boot_backfill_customer_locations(db: AsyncIOMotorDatabase) -> dict:
    """Boot task: bounded (20 s a boot), resumable, idempotent."""
    return await backfill_customer_locations(db)


# -- Staff list filters / sorting ------------------------------------------

SORTABLE = {
    "created_at": "created_at", "full_name": "full_name", "center": "home_center_id", "home_center_id": "home_center_id",
    "city": "home_city", "home_city": "home_city", "pincode": "home_pincode", "home_pincode": "home_pincode",
    "bookings_count": "bookings_count", "last_booking_at": "last_booking_at",
}


def location_filters(center_id: Optional[str] = None, city: Optional[str] = None, pincode: Optional[str] = None) -> dict:
    """users-collection filter for the three location facets (exact match, so
    the (role, home_*) indexes serve it)."""
    q: dict = {}
    if center_id:
        q["home_center_id"] = center_id
    if city:
        q["home_city"] = city.strip()
    if pincode:
        q["home_pincode"] = pincode.strip()
    return q


async def location_facets(db: AsyncIOMotorDatabase) -> dict:
    cities = await db.users.distinct("home_city", {"role": "customer", "home_city": {"$ne": None}})
    return {"cities": sorted(c for c in cities if c)}
