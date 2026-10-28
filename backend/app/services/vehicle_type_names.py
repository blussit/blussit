"""One place that turns a vehicle-type id into a name — for ever.

A vehicle type that an admin deletes is only SOFT-deleted (the doc stays,
`is_deleted: true`), and types that were removed before that rule existed
are rebuilt as soft-deleted stub docs by `backfill_vehicle_type_tombstones`.
So a historical booking / pass / plan / invoice always resolves its ORIGINAL
name, and an id nobody can resolve prints REMOVED_LABEL — never the hash.

`VehicleTypeNames` is the resolver used by serializers and enrichment
(60 s cache; the vehicle-type service busts it on every write; an unknown id
forces one throttled refresh, so a type created a moment ago still shows).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

REMOVED_LABEL = "Removed Car Type"
_TTL_SECONDS = 60.0
_UNKNOWN_REFRESH_SECONDS = 2.0
_HEX24 = re.compile(r"^[0-9a-fA-F]{24}$")

# db name -> {"at": monotonic, "map": {id: {"name", "removed", ...}}}
_CACHE: dict[str, dict] = {}


def looks_like_id(value) -> bool:
    return isinstance(value, str) and bool(_HEX24.match(value))


def clean_name(value) -> str:
    """A display-safe name: blank or a raw id becomes REMOVED_LABEL."""
    text = str(value or "").strip()
    return REMOVED_LABEL if not text or looks_like_id(text) else text


class VehicleTypeNames:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    @staticmethod
    def invalidate(db: Optional[AsyncIOMotorDatabase] = None) -> None:
        if db is None:
            _CACHE.clear()
        else:
            _CACHE.pop(db.name, None)

    async def _load(self, *, force: bool = False) -> dict[str, dict]:
        slot = _CACHE.get(self.db.name)
        now = time.monotonic()
        if slot and not force and now - slot["at"] < _TTL_SECONDS:
            return slot["map"]
        rows = await self.db.vehicle_types.find(
            {}, {"name": 1, "is_deleted": 1, "is_active": 1, "display_order": 1, "deleted_at": 1, "tombstone": 1}
        ).to_list(length=None)
        mapping = {
            str(r["_id"]): {
                "id": str(r["_id"]),
                "name": clean_name(r.get("name")),
                "removed": bool(r.get("is_deleted")),
                "is_active": r.get("is_active", True) is not False,
                "display_order": r.get("display_order") or 0,
            }
            for r in rows
        }
        _CACHE[self.db.name] = {"at": now, "map": mapping, "refreshed_for_unknown": 0.0}
        return mapping

    async def _map_for(self, ids: Iterable) -> dict[str, dict]:
        mapping = await self._load()
        wanted = {str(i) for i in ids if i}
        if wanted - mapping.keys():
            slot = _CACHE.get(self.db.name) or {}
            now = time.monotonic()
            if now - slot.get("refreshed_for_unknown", 0.0) > _UNKNOWN_REFRESH_SECONDS:
                mapping = await self._load(force=True)
                _CACHE[self.db.name]["refreshed_for_unknown"] = now
        return mapping

    async def names(self, ids: Iterable) -> dict[str, str]:
        """{id: name} for every id asked — live, removed or unknown alike."""
        mapping = await self._map_for(ids)
        return {str(i): (mapping.get(str(i)) or {}).get("name") or REMOVED_LABEL for i in ids if i}

    async def name(self, vehicle_type_id) -> Optional[str]:
        if not vehicle_type_id:
            return None
        return (await self.names([vehicle_type_id]))[str(vehicle_type_id)]

    async def is_removed(self, vehicle_type_id) -> bool:
        mapping = await self._map_for([vehicle_type_id])
        entry = mapping.get(str(vehicle_type_id))
        return entry is None or entry["removed"]

    async def entries(self) -> list[dict]:
        """Every type, live first (display order), then removed ones."""
        mapping = await self._load(force=True)
        return sorted(
            ({k: v[k] for k in ("id", "name", "removed", "is_active")} for v in mapping.values()),
            key=lambda e: (e["removed"], mapping[e["id"]]["display_order"], e["name"]),
        )


async def vehicle_type_names(db: AsyncIOMotorDatabase, ids: Iterable) -> dict[str, str]:
    return await VehicleTypeNames(db).names(list(ids))


# ── Boot backfill: rebuild tombstones for types deleted before soft delete ──

_NAME_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bookings", ("vehicle_label", "vehicle_type_name")),
    ("user_subscriptions", ("vehicle_type_name", "vehicle_label", "car_label")),
    ("vehicles", ("vehicle_type_name", "vehicle_label")),
    ("payment_orders", ("vehicle_type_name", "vehicle_label")),
)


async def backfill_vehicle_type_tombstones(db: AsyncIOMotorDatabase) -> dict:
    """Idempotent. Every vehicle-type id that bookings / vehicles / passes /
    orders still point at but that has NO vehicle_types doc gets a
    soft-deleted stub doc `{_id: old id, name, is_deleted: true, tombstone:
    true}` — every existing lookup (they all read the raw collection) then
    resolves it. The name is the most common one stored on the records
    themselves; with none anywhere it is REMOVED_LABEL. A stub still named
    REMOVED_LABEL is upgraded when a stored name turns up later."""
    existing = {str(d["_id"]): d for d in await db.vehicle_types.find({}, {"name": 1, "tombstone": 1}).to_list(length=None)}
    ids: set[str] = set()
    for coll, _fields in _NAME_SOURCES:
        try:
            ids.update(v for v in await db[coll].distinct("vehicle_type") if looks_like_id(v))
        except Exception:  # noqa: BLE001 — a collection this build lacks
            continue
    for coll in ("services", "subscription_plans", "coupons"):
        try:
            ids.update(v for v in await db[coll].distinct("vehicle_types") if looks_like_id(v))
        except Exception:  # noqa: BLE001
            continue
    missing = {i for i in ids if i not in existing}
    upgradable = {i for i, d in existing.items() if d.get("tombstone") and d.get("name") == REMOVED_LABEL}
    todo = missing | upgradable
    if not todo:
        return {"created": 0, "upgraded": 0}

    votes: dict[str, dict[str, int]] = {}
    for coll, fields in _NAME_SOURCES:
        for field in fields:
            try:
                rows = await db[coll].aggregate([
                    {"$match": {"vehicle_type": {"$in": list(todo)}, field: {"$type": "string", "$ne": ""}}},
                    {"$group": {"_id": {"t": "$vehicle_type", "n": f"${field}"}, "c": {"$sum": 1}}},
                ], allowDiskUse=True).to_list(length=None)
            except Exception:  # noqa: BLE001
                continue
            for r in rows:
                name = str(r["_id"]["n"]).strip()
                if name and not looks_like_id(name):
                    votes.setdefault(r["_id"]["t"], {})[name] = votes.setdefault(r["_id"]["t"], {}).get(name, 0) + r["c"]
    now = datetime.now(timezone.utc)
    created = upgraded = 0
    for vt_id in todo:
        ballots = votes.get(vt_id) or {}
        name = max(ballots, key=lambda n: ballots[n]) if ballots else REMOVED_LABEL
        if vt_id in missing:
            res = await db.vehicle_types.update_one(
                {"_id": ObjectId(vt_id)},
                {"$setOnInsert": {
                    "name": name, "is_deleted": True, "is_active": False, "tombstone": True, "display_order": 9999,
                    "deleted_at": now, "deleted_by": "system:backfill", "created_at": now, "updated_at": now,
                }},
                upsert=True,
            )
            created += 1 if res.upserted_id else 0
        elif name != REMOVED_LABEL:
            await db.vehicle_types.update_one(
                {"_id": ObjectId(vt_id), "tombstone": True, "name": REMOVED_LABEL}, {"$set": {"name": name, "updated_at": now}}
            )
            upgraded += 1
    VehicleTypeNames.invalidate(db)
    return {"created": created, "upgraded": upgraded}
