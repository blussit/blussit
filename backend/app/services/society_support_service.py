"""
Society support — see docs/SOCIETY_PLANS.md §9.

* Resident issues: a society resident's problem ("Daily wash missed",
  "Captain didn't arrive"…) becomes an ordinary support ticket in the
  complaints collection, tagged with the society (`category: "society"`,
  `society_id`, `issue_type`, the car), so the existing thread, drawer and
  center scoping all apply: managers see their center's societies' issues,
  admin all, the resident only their own.
* Society requests (leads): "bring Blussit to our society" from the public
  landing page. Routed to a center by pincode (the same resolver bookings
  use); unmatched ones are admin's. Managers/admin work them through
  new → contacted → registered / closed, and "Register this society" links
  the new society back to the request.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.models.enums import ComplaintStatus, NotificationType
from app.schemas.society_schema import LEAD_STATUSES, SOCIETY_ISSUE_TYPES
from app.utils.money import round_rupees

logger = logging.getLogger(__name__)

OPEN_ISSUE_STATUSES = [ComplaintStatus.OPEN.value, ComplaintStatus.IN_PROGRESS.value]
ISSUE_PRIORITY = {"daily_wash_missed": "high", "captain_no_show": "high"}
# The same issue on the same car isn't filed twice while it's still open.
ISSUE_DEDUPE_WINDOW = timedelta(hours=12)
LEAD_DEDUPE_WINDOW = timedelta(hours=24)
MAX_ROWS = 200


def _iso(dt) -> str | None:
    from app.utils.timezone import from_stored

    return from_stored(dt).isoformat() if dt else None


async def _notify(db, user_ids: list[str], title: str, message: str, reference_id: str | None, kind: NotificationType) -> None:
    """In-app only; an alert must never fail the action that raised it."""
    from app.services.notification_service import NotificationService

    try:
        notifications = NotificationService(db)
        for uid in dict.fromkeys(user_ids):
            await notifications.notify(uid, title, message, kind, reference_id, send_whatsapp=False)
    except Exception:  # noqa: BLE001
        logger.exception("Could not send '%s' alerts", title)


async def _center_managers(db, center_id: str | None) -> list[str]:
    if not center_id:
        return []
    rows = await db.users.find(
        {"role": "manager", "service_center_id": center_id, "is_deleted": {"$ne": True}, "status": {"$nin": ["suspended", "inactive"]}}, {"_id": 1}
    ).to_list(length=20)
    return [str(r["_id"]) for r in rows]


async def _admins(db) -> list[str]:
    rows = await db.users.find({"role": "admin", "is_deleted": {"$ne": True}, "status": {"$nin": ["suspended", "inactive"]}}, {"_id": 1}).to_list(length=10)
    return [str(r["_id"]) for r in rows]


class SocietyIssueService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def _type_name(self, vehicle_type: str | None) -> str | None:
        if not vehicle_type or not ObjectId.is_valid(str(vehicle_type)):
            return None
        row = await self.db.vehicle_types.find_one({"_id": ObjectId(str(vehicle_type))}, {"name": 1})
        return (row or {}).get("name") or None

    async def _with_car_types(self, rows: list[dict]) -> list[dict]:
        """Issue rows carry `vehicle_type_name` ("Hatchback") next to the
        plate. Issues filed before it was stored get it from the car (two
        bounded reads for the whole page)."""
        missing = {str(r["vehicle_id"]) for r in rows if r.get("vehicle_id") and not r.get("vehicle_type_name") and ObjectId.is_valid(str(r["vehicle_id"]))}
        if not missing:
            return rows
        vehicles = await self.db.vehicles.find({"_id": {"$in": [ObjectId(v) for v in missing]}}, {"vehicle_type": 1}).to_list(length=len(missing))
        type_of = {str(v["_id"]): str(v.get("vehicle_type") or "") for v in vehicles}
        type_ids = [ObjectId(t) for t in set(type_of.values()) if ObjectId.is_valid(t)]
        names = {str(t["_id"]): t.get("name") for t in await self.db.vehicle_types.find({"_id": {"$in": type_ids}}, {"name": 1}).to_list(length=len(type_ids) or 1)} if type_ids else {}
        for r in rows:
            if r.get("vehicle_id") and not r.get("vehicle_type_name"):
                r["vehicle_type_name"] = names.get(type_of.get(str(r["vehicle_id"])) or "") or None
        return rows

    async def raise_issue(self, customer_id: str, payload) -> dict:
        """A resident of THIS society (any enrollment of theirs here) files
        an issue. Anyone else gets a 404 — a guessed society id confirms
        nothing."""
        from app.repositories.complaint_repository import ComplaintRepository
        from app.services.complaint_service import ComplaintService

        society = await self.db.societies.find_one({"_id": ObjectId(payload.society_id), "is_deleted": {"$ne": True}})
        enrollments = await self.db.society_enrollments.find(
            {"society_id": payload.society_id, "customer_id": customer_id, "is_deleted": {"$ne": True}}
        ).sort("created_at", -1).to_list(length=20) if society else []
        if not society or not enrollments:
            raise NotFoundException("Society not found")
        car = None
        if payload.vehicle_id:
            car = next((c for e in enrollments for c in e.get("cars") or [] if c.get("vehicle_id") == payload.vehicle_id), None)
            if not car:
                raise BadRequestException("Pick one of your cars on this society plan.")
        sid = str(society["_id"])
        now = datetime.now(timezone.utc)
        duplicate = await self.db.complaints.find_one({
            "society_id": sid, "customer_id": customer_id, "issue_type": payload.issue_type, "vehicle_id": payload.vehicle_id,
            "status": {"$in": OPEN_ISSUE_STATUSES}, "created_at": {"$gte": now - ISSUE_DEDUPE_WINDOW}, "is_deleted": {"$ne": True},
        }, {"_id": 1})
        if duplicate:
            raise ConflictException("You've already reported this — your society manager is on it.")
        label = SOCIETY_ISSUE_TYPES[payload.issue_type]
        live = next((e for e in enrollments if e.get("status") != "cancelled"), enrollments[0])
        flat = live.get("flat")
        plate = (car or {}).get("registration_number")
        car_type = await self._type_name((car or {}).get("vehicle_type"))
        detail = " · ".join(x for x in (flat, plate) if x)
        doc = {
            "customer_id": customer_id,
            "service_center_id": society.get("service_center_id"),
            "booking_id": None,
            "subject": f"{label} — {society.get('name')}"[:150],
            "description": payload.note or f"{label}{f' ({detail})' if detail else ''}.",
            "priority": ISSUE_PRIORITY.get(payload.issue_type, "medium"),
            "attachments": [],
            "status": ComplaintStatus.OPEN.value,
            "replies": [],
            # The society tag — what the "Society issues" filters key on.
            "category": "society",
            "society_id": sid,
            "society_name": society.get("name"),
            "issue_type": payload.issue_type,
            "issue_label": label,
            "vehicle_id": payload.vehicle_id,
            "registration_number": plate,
            "vehicle_type_name": car_type,
            "flat": flat,
            "enrollment_id": str(live["_id"]),
        }
        created = await ComplaintRepository(self.db).create(doc)
        recipients = await _center_managers(self.db, society.get("service_center_id")) or await _admins(self.db)
        await _notify(
            self.db, recipients, "Society issue",
            f"{society.get('name')}{f' ({detail})' if detail else ''}: {label}.", str(created["_id"]), NotificationType.COMPLAINT,
        )
        return (await ComplaintService(self.db)._enrich([created]))[0]

    async def my_issues(self, customer_id: str, society_id: str | None) -> list[dict]:
        from app.services.complaint_service import ComplaintService

        query: dict = {"customer_id": customer_id, "category": "society", "is_deleted": {"$ne": True}}
        if society_id:
            query["society_id"] = society_id
        rows = await self.db.complaints.find(query).sort("created_at", -1).to_list(length=50)
        return await self._with_car_types(await ComplaintService(self.db)._enrich(rows))

    async def society_issues(self, society: dict, status: str | None) -> list[dict]:
        """Staff: one society's issues (caller already scoped the society)."""
        from app.services.complaint_service import ComplaintService

        query: dict = {"society_id": str(society["_id"]), "is_deleted": {"$ne": True}}
        if status == "open":
            query["status"] = {"$in": OPEN_ISSUE_STATUSES}
        elif status:
            query["status"] = status
        rows = await self.db.complaints.find(query).sort("created_at", -1).to_list(length=MAX_ROWS)
        return await self._with_car_types(await ComplaintService(self.db)._enrich(rows))


async def open_issue_counts(db, society_ids: list[str]) -> dict[str, int]:
    if not society_ids:
        return {}
    rows = await db.complaints.aggregate([
        {"$match": {"society_id": {"$in": society_ids}, "status": {"$in": OPEN_ISSUE_STATUSES}, "is_deleted": {"$ne": True}}},
        {"$group": {"_id": "$society_id", "n": {"$sum": 1}}},
    ]).to_list(length=None)
    return {r["_id"]: r["n"] for r in rows}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


class SocietyLeadService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db["society_leads"]

    async def _center_for(self, pincode: str) -> dict | None:
        """The center serving this pincode — the bookings resolver's pincode
        path (no pin on a lead). None = unmatched: admin's to route."""
        from app.services.booking_service import BookingService

        try:
            center, _ = await BookingService(self.db)._resolve_service_center({"pincode": pincode}, allow_pinless=True)
            return center
        except BadRequestException:
            return None

    async def capture(self, payload) -> bool:
        """Stores (or, for the same phone + society within 24 h, merges)
        one request. True when it's new. Returns nothing about anyone."""
        now = datetime.now(timezone.utc)
        key = _name_key(payload.society_name)
        fields = {
            "society_name": payload.society_name,
            "area": payload.area,
            "pincode": payload.pincode,
            "contact_name": payload.contact_name,
            "approx_cars": payload.approx_cars,
            "note": payload.note,
            "updated_at": now,
        }
        existing = await self.collection.find_one_and_update(
            {"phone": payload.phone, "name_key": key, "created_at": {"$gte": now - LEAD_DEDUPE_WINDOW}, "is_deleted": {"$ne": True}},
            {"$set": fields, "$inc": {"requests_count": 1}},
        )
        if existing:
            return False
        center = await self._center_for(payload.pincode)
        center_id = str(center["_id"]) if center else None
        doc = {
            **fields,
            "phone": payload.phone,
            "name_key": key,
            "service_center_id": center_id,
            "status": "new",
            "requests_count": 1,
            "society_id": None,
            "history": [],
            "created_at": now,
            "is_deleted": False,
        }
        result = await self.collection.insert_one(doc)
        recipients = await _center_managers(self.db, center_id) if center_id else []
        if not recipients:
            recipients = await _admins(self.db)
        await _notify(
            self.db, recipients, "New society request",
            f"{payload.society_name}, {payload.area} ({payload.pincode}) — about {payload.approx_cars} cars. Call {payload.contact_name}.",
            str(result.inserted_id), NotificationType.SYSTEM,
        )
        return True

    async def from_price(self) -> int | None:
        """'From ₹X / car / month' for the landing pitch: the cheapest
        active template society plan, any car type. Only a price — never
        the plans themselves (they stay off the public catalogue)."""
        plans = await self.db.subscription_plans.find(
            {"plan_type": "society", "is_active": True, "is_deleted": {"$ne": True}, "society_scope": {"$in": ["template", None]}},
            {"discounted_price": 1, "price": 1, "vehicle_type_discounted_prices": 1},
        ).to_list(length=100)
        prices: list[float] = []
        for p in plans:
            per_type = [v for v in (p.get("vehicle_type_discounted_prices") or {}).values() if v]
            prices.extend(per_type or [p.get("discounted_price") or p.get("price") or 0])
        prices = [x for x in prices if x and x > 0]
        return round_rupees(min(prices)) if prices else None

    # -- staff ---------------------------------------------------------------

    def _scope(self, actor_role: str, actor_center_id: str | None, center_id: str | None) -> dict:
        query: dict = {"is_deleted": {"$ne": True}}
        if actor_role == "manager":
            if not actor_center_id:
                raise BadRequestException("Your account isn't linked to a service center yet.")
            query["service_center_id"] = actor_center_id
        elif center_id == "unassigned":
            query["service_center_id"] = None
        elif center_id:
            query["service_center_id"] = center_id
        return query

    async def list(self, actor_role: str, actor_center_id: str | None, status: str | None = None, center_id: str | None = None) -> dict:
        query = self._scope(actor_role, actor_center_id, center_id)
        base = dict(query)
        if status == "open":
            query["status"] = {"$in": ["new", "contacted"]}
        elif status:
            query["status"] = status
        rows = await self.collection.find(query).sort("created_at", -1).to_list(length=MAX_ROWS)
        counts = {r["_id"]: r["n"] for r in await self.collection.aggregate([
            {"$match": base}, {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]).to_list(length=None)}
        center_ids = list({r.get("service_center_id") for r in rows if r.get("service_center_id")})
        centers = {}
        if center_ids:
            found = await self.db.service_centers.find({"_id": {"$in": [ObjectId(c) for c in center_ids if ObjectId.is_valid(c)]}}, {"name": 1}).to_list(length=len(center_ids))
            centers = {str(c["_id"]): c.get("name") for c in found}
        return {"rows": [self._view(r, centers) for r in rows], "counts": {s: counts.get(s, 0) for s in LEAD_STATUSES}}

    @staticmethod
    def _view(r: dict, centers: dict[str, str]) -> dict:
        return {
            "id": str(r["_id"]),
            "society_name": r.get("society_name"),
            "area": r.get("area"),
            "pincode": r.get("pincode"),
            "contact_name": r.get("contact_name"),
            "phone": r.get("phone"),
            "approx_cars": r.get("approx_cars"),
            "note": r.get("note"),
            "staff_note": r.get("staff_note"),
            "status": r.get("status") or "new",
            "requests_count": r.get("requests_count") or 1,
            "service_center_id": r.get("service_center_id"),
            "service_center_name": centers.get(r.get("service_center_id") or ""),
            "society_id": r.get("society_id"),
            "created_at": _iso(r.get("created_at")),
            "updated_at": _iso(r.get("updated_at")),
        }

    async def _lead_for(self, lead_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        lead = await self.collection.find_one({"_id": ObjectId(lead_id), "is_deleted": {"$ne": True}}) if ObjectId.is_valid(lead_id or "") else None
        if not lead:
            raise NotFoundException("Request not found")
        if actor_role == "admin":
            return lead
        if actor_role != "manager" or not lead.get("service_center_id"):
            raise ForbiddenException("You don't have access to this request")
        ensure_own_center(actor_role, actor_center_id, lead["service_center_id"])
        return lead

    async def update(self, lead_id: str, payload, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        lead = await self._lead_for(lead_id, actor_role, actor_center_id)
        data = payload.model_dump(exclude_unset=True)
        now = datetime.now(timezone.utc)
        update: dict = {"$set": {"updated_at": now}}
        if data.get("status") and data["status"] != lead.get("status"):
            update["$set"]["status"] = data["status"]
            update["$push"] = {"history": {"status": data["status"], "by": actor_id, "at": now}}
        if "staff_note" in data:
            update["$set"]["staff_note"] = (data.get("staff_note") or "").strip() or None
        fresh = await self.collection.find_one_and_update({"_id": lead["_id"]}, update, return_document=ReturnDocument.AFTER)
        return self._view(fresh, await self._center_names([fresh.get("service_center_id")]))

    async def claim_for_registration(self, lead_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> dict:
        """Insert-first claim before a society is created from this request
        (SOC-6): flips it to "registered" atomically, so two simultaneous
        "Register this society" taps (or two staff) can't both create one.
        Returns the request as it was, for release_registration."""
        lead = await self._lead_for(lead_id, actor_role, actor_center_id)
        now = datetime.now(timezone.utc)
        before = await self.collection.find_one_and_update(
            {"_id": lead["_id"], "status": {"$ne": "registered"}, "is_deleted": {"$ne": True}},
            {"$set": {"status": "registered", "registering_by": actor_id, "updated_at": now}},
        )
        if not before:
            raise ConflictException("This society request is already registered as a society.")
        return before

    async def release_registration(self, before: dict) -> None:
        """The society couldn't be created after all: the request goes back
        to the status it had (only if nothing linked a society meanwhile)."""
        await self.collection.update_one(
            {"_id": before["_id"], "status": "registered", "society_id": before.get("society_id")},
            {"$set": {"status": before.get("status") or "new", "updated_at": datetime.now(timezone.utc)}, "$unset": {"registering_by": ""}},
        )

    async def mark_registered(self, lead_id: str, society: dict, actor_id: str, actor_role: str, actor_center_id: str | None) -> None:
        """Register-this-society shortcut: the request becomes 'registered'
        and points at the new society (and its center)."""
        lead = await self._lead_for(lead_id, actor_role, actor_center_id)
        now = datetime.now(timezone.utc)
        await self.collection.update_one({"_id": lead["_id"]}, {
            "$set": {"status": "registered", "society_id": society["id"], "service_center_id": society.get("service_center_id"), "updated_at": now},
            "$push": {"history": {"status": "registered", "by": actor_id, "at": now}},
        })

    async def _center_names(self, ids) -> dict[str, str]:
        oids = [ObjectId(i) for i in ids if i and ObjectId.is_valid(i)]
        if not oids:
            return {}
        rows = await self.db.service_centers.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))
        return {str(r["_id"]): r.get("name") for r in rows}

