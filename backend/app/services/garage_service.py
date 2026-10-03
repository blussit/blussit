"""
My Garage (GET /vehicles/garage): every car the customer has — the ones
they saved AND every car they've had washed — one row per car.

A car is identified, in this order, by
  1. its saved vehicle record (vehicle_id),
  2. its registration plate (case/space-insensitive, normalize_plate),
  3. only its vehicle TYPE when no plate exists (quick-booking model) —
     so a plateless XUV washed twice is one "XUV" row.

Merging rules (merge_garage, pure and unit-tested):
  - a saved vehicle that was also booked appears once, carrying its
    "Last washed" / "Next wash" from those bookings;
  - plateless bookings of a type the customer has SAVED a vehicle of are
    that saved vehicle (the first of the type in garage order — default,
    then newest) instead of a second row;
  - bookings tied to a vehicle the customer REMOVED from the garage stay
    hidden (removing a car is deliberate), unless the same plate is saved
    again, in which case they join that saved car.

Bounded: the customer's saved vehicles (capped at 50) + one indexed
aggregation over their newest bookings (see BookingRepository.garage_groups).
"""
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.booking_repository import BookingRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.utils.text import normalize_plate
from app.utils.timezone import now_ist, to_ist

GARAGE_LIMIT = 50
_EPOCH = datetime(1970, 1, 1)


def _naive(dt) -> datetime:
    """A sortable naive datetime (created_at reads back naive UTC; an
    aware value is folded to the same form; missing sorts oldest)."""
    if not isinstance(dt, datetime):
        return _EPOCH
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _day(dt) -> str | None:
    """scheduled_date is stored naive with its IST wall-clock digits."""
    return to_ist(dt).strftime("%Y-%m-%d") if isinstance(dt, datetime) else None


def _next_key(n: dict) -> tuple:
    return (_naive(n.get("d")), str(n.get("s") or ""))


def _repeat_key(r: dict) -> tuple:
    return (int(r.get("done") or 0), _naive(r.get("d")), str(r.get("s") or ""))


def _new_row(key: str, *, vehicle: dict | None = None, vehicle_type: str | None = None, plate: str | None = None) -> dict:
    return {
        "id": key,
        "vehicle_id": str(vehicle["_id"]) if vehicle else None,
        "saved": vehicle is not None,
        "is_default": bool(vehicle.get("is_default")) if vehicle else False,
        "vehicle_type": (vehicle or {}).get("vehicle_type") or vehicle_type,
        "brand": (vehicle or {}).get("brand") or None,
        "model": (vehicle or {}).get("model") or None,
        "registration_number": (vehicle or {}).get("registration_number") or plate or None,
        "vehicle_label": None,
        "_activity": _naive((vehicle or {}).get("created_at")),
        "_last_booking_at": None,
        "last_booking_id": None,
        "_last_washed": None,
        "wash_count": 0,
        "_next": None,
        "_repeat": None,
        "_last_wash": None,
    }


def _attach(row: dict, group: dict) -> None:
    """Fold one booking group's numbers into a car row."""
    latest = _naive(group.get("latest_at"))
    if row["_last_booking_at"] is None or latest > row["_last_booking_at"]:
        row["_last_booking_at"] = latest
        row["last_booking_id"] = str(group["last_booking_id"]) if group.get("last_booking_id") else None
    row["_activity"] = max(row["_activity"], latest)
    if not row["vehicle_type"] and group.get("vehicle_type"):
        row["vehicle_type"] = group["vehicle_type"]
    if not row["vehicle_label"] and group.get("vehicle_label"):
        row["vehicle_label"] = group["vehicle_label"]
    washed = group.get("last_washed_on")
    if isinstance(washed, datetime) and (row["_last_washed"] is None or _naive(washed) > _naive(row["_last_washed"])):
        row["_last_washed"] = washed
    row["wash_count"] += int(group.get("wash_count") or 0)
    nxt = group.get("next")
    if nxt and (row["_next"] is None or _next_key(nxt) < _next_key(row["_next"])):
        row["_next"] = nxt
    rep = group.get("repeat")
    if rep and (row["_repeat"] is None or _repeat_key(rep) > _repeat_key(row["_repeat"])):
        row["_repeat"] = rep
    last = group.get("last_wash")
    if last and (row["_last_wash"] is None or _next_key(last) > _next_key(row["_last_wash"])):
        row["_last_wash"] = last


def _service_names(ids, service_names: dict[str, str]) -> str | None:
    names = [service_names[i] for i in (ids or []) if service_names.get(i)]
    return " + ".join(names) or None


def _public(row: dict, type_names: dict[str, str], service_names: dict[str, str] | None = None) -> dict:
    nxt, rep, last = row["_next"], row["_repeat"], row["_last_wash"]
    service_names = service_names or {}
    return {
        "id": row["id"],
        "vehicle_id": row["vehicle_id"],
        "saved": row["saved"],
        "is_default": row["is_default"],
        "vehicle_type": row["vehicle_type"],
        "vehicle_type_name": type_names.get(row["vehicle_type"] or "") or row["vehicle_label"] or "Vehicle",
        "brand": row["brand"],
        "model": row["model"],
        "registration_number": row["registration_number"],
        "last_washed_on": _day(row["_last_washed"]),
        "wash_count": row["wash_count"],
        "next_wash_on": _day(nxt.get("d")) if nxt else None,
        "next_wash_slot": nxt.get("s") if nxt else None,
        "next_booking_id": str(nxt["id"]) if nxt and nxt.get("id") else None,
        "repeat_booking_id": str(rep["id"]) if rep and rep.get("id") else None,
        "last_booking_id": row["last_booking_id"],
        "next_service_name": _service_names(nxt.get("svc"), service_names) if nxt else None,
        "last_wash_booking_id": str(last["id"]) if last and last.get("id") else None,
        "last_wash_service_name": _service_names(last.get("svc"), service_names) if last else None,
    }


def merge_garage(
    vehicles: list[dict],
    groups: list[dict],
    *,
    type_names: dict[str, str] | None = None,
    removed_vehicle_ids: set[str] | frozenset[str] = frozenset(),
    other_vehicles: dict[str, dict] | None = None,
    limit: int = GARAGE_LIMIT,
    service_names: dict[str, str] | None = None,
) -> list[dict]:
    """Saved `vehicles` (garage order: default first, then newest) +
    booking `groups` (BookingRepository.garage_groups rows) -> one row per
    car, most recent activity first. `other_vehicles` are live vehicle
    records a booking points at that aren't in `vehicles` (their plate
    names the car); `removed_vehicle_ids` are ones the customer deleted."""
    type_names = type_names or {}
    other_vehicles = other_vehicles or {}
    rows: list[dict] = []
    by_vid: dict[str, dict] = {}
    by_plate: dict[str, dict] = {}
    saved_by_type: dict[str, dict] = {}
    for v in vehicles:
        row = _new_row(f"v:{v['_id']}", vehicle=v)
        rows.append(row)
        by_vid[str(v["_id"])] = row
        plate = normalize_plate(v.get("registration_number") or "")
        if plate:
            by_plate.setdefault(plate, row)
        if v.get("vehicle_type"):
            saved_by_type.setdefault(v["vehicle_type"], row)
    saved_plates = set(by_plate)

    type_rows: dict[str, dict] = {}
    plateless: list[dict] = []
    for g in groups:
        key = str(g.get("_id") or "")
        kind, _, ident = key.partition(":")
        if kind == "v":
            if ident in by_vid:
                _attach(by_vid[ident], g)
                continue
            known = other_vehicles.get(ident) or {}
            plate = normalize_plate(g.get("plate") or known.get("registration_number") or "")
            if plate and plate in saved_plates:
                _attach(by_plate[plate], g)
            elif ident in removed_vehicle_ids:
                continue
            elif plate:
                if plate not in by_plate:
                    row = _new_row(f"p:{plate}", vehicle_type=g.get("vehicle_type") or known.get("vehicle_type"), plate=known.get("registration_number") or plate)
                    if known:
                        row["brand"], row["model"] = known.get("brand") or None, known.get("model") or None
                    rows.append(row)
                    by_plate[plate] = row
                _attach(by_plate[plate], g)
            else:
                plateless.append(g)
        elif kind == "p":
            plate = normalize_plate(ident)
            if not plate:
                plateless.append(g)
                continue
            if plate not in by_plate:
                row = _new_row(f"p:{plate}", vehicle_type=g.get("vehicle_type"), plate=plate)
                rows.append(row)
                by_plate[plate] = row
            _attach(by_plate[plate], g)
        else:
            plateless.append(g)

    # Plateless washes last, so they can fold into a saved car of the type.
    for g in plateless:
        vtype = g.get("vehicle_type") or ""
        if vtype in saved_by_type:
            _attach(saved_by_type[vtype], g)
            continue
        if vtype not in type_rows:
            row = _new_row(f"t:{vtype}", vehicle_type=vtype or None)
            rows.append(row)
            type_rows[vtype] = row
        _attach(type_rows[vtype], g)

    saved = [r for r in rows if r["saved"]]
    history = sorted((r for r in rows if not r["saved"]), key=lambda r: r["_activity"], reverse=True)
    kept = saved + history[: max(0, limit - len(saved))]
    kept.sort(key=lambda r: r["_activity"], reverse=True)
    return [_public(r, type_names, service_names) for r in kept]


class GarageService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.vehicle_repo = VehicleRepository(db)
        self.booking_repo = BookingRepository(db)
        self.vehicle_type_repo = VehicleTypeRepository(db)

    async def list_for_customer(self, customer_id: str) -> list[dict]:
        vehicles = await self.vehicle_repo.list_by_owner(customer_id)
        today = now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        groups = await self.booking_repo.garage_groups(customer_id, today, limit=GARAGE_LIMIT)

        saved_ids = {str(v["_id"]) for v in vehicles}
        foreign = [
            g["_id"][2:] for g in groups
            if str(g.get("_id") or "").startswith("v:") and g["_id"][2:] not in saved_ids and ObjectId.is_valid(g["_id"][2:])
        ]
        removed: set[str] = set()
        others: dict[str, dict] = {}
        if foreign:
            projection = {"is_deleted": 1, "owner_id": 1, "registration_number": 1, "vehicle_type": 1, "brand": 1, "model": 1}
            async for v in self.db.vehicles.find({"_id": {"$in": [ObjectId(i) for i in foreign]}}, projection):
                if v.get("is_deleted") or v.get("owner_id") != customer_id:
                    removed.add(str(v["_id"]))
                else:
                    others[str(v["_id"])] = v

        type_ids = {v.get("vehicle_type") for v in vehicles} | {g.get("vehicle_type") for g in groups}
        type_ids |= {v.get("vehicle_type") for v in others.values()}
        types = await self.vehicle_type_repo.find_by_ids([t for t in type_ids if t])
        type_names = {str(t["_id"]): t.get("name") or "" for t in types}
        # Names for the next/last wash services — one batched lookup.
        service_ids = {str(i) for g in groups for key in ("next", "last_wash") for i in ((g.get(key) or {}).get("svc") or [])}
        services = await self.db.services.find(
            {"_id": {"$in": [ObjectId(i) for i in service_ids if ObjectId.is_valid(i)]}}, {"name": 1}
        ).to_list(length=200) if service_ids else []
        service_names = {str(x["_id"]): x.get("name") or "" for x in services}
        return merge_garage(
            vehicles, groups, type_names=type_names, removed_vehicle_ids=removed, other_vehicles=others, service_names=service_names,
        )
