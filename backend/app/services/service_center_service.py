from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.service_center_repository import ServiceCenterRepository
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest, ensure_opens_before_closes
from app.utils.serializers import serialize_doc, serialize_list


# Fields whose change re-shapes the center's slots (and so its capacity
# bookkeeping) — see BookingService.slot_change_blockers.
_SLOT_SHAPE_FIELDS = ("working_hours_start", "working_hours_end", "slot_duration_minutes")
# What still depends on a center — it can't be deleted while any exists.
_LIVE_BOOKING_STATUSES = ["awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]


class ServiceCenterService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = ServiceCenterRepository(db)

    async def list_centers(self, page: int, page_size: int, search: str | None, active_only: bool = False):
        items, total = await self.repo.search(search, page, page_size, active_only)
        return serialize_list(items), total

    async def get(self, center_id: str) -> dict:
        center = await self.repo.find_by_id(center_id)
        if not center:
            raise NotFoundException("Service center not found")
        return serialize_doc(center)

    async def find_for_pincode(self, pincode: str) -> list[dict]:
        """Anonymous lookup: which center serves a pincode — its id, name and
        town. The raw doc also named its manager and private contacts."""
        centers = serialize_list(await self.repo.find_by_pincode(pincode))
        return [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "location": {k: (c.get("location") or {}).get(k) for k in ("city", "state", "pincode")},
                "working_hours_start": c.get("working_hours_start"),
                "working_hours_end": c.get("working_hours_end"),
                "is_active": c.get("is_active", True),
            }
            for c in centers
        ]

    async def create(self, payload: ServiceCenterCreateRequest) -> dict:
        doc = payload.model_dump()
        # Stored explicitly: every lookup (nearest, pincode, bot, KPIs)
        # filters on it (QA 2026-10-07 — a new center was invisible).
        doc["is_active"] = True
        doc["code"] = self.repo.generate_code(payload.location.city)
        created = await self.repo.create(doc)
        return serialize_doc(created)

    # Optional fields an admin can clear again (sent as null): unassigning
    # the manager stops that person's new-booking WhatsApps for this center.
    _CLEARABLE = ("manager_id", "contact_phone", "contact_email", "slot_duration_minutes")

    async def update(self, center_id: str, payload: ServiceCenterUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in self._CLEARABLE}
        if data.get("manager_id"):
            from bson import ObjectId

            manager = (
                await self.repo.db.users.find_one({"_id": ObjectId(data["manager_id"]), "role": "manager", "is_deleted": {"$ne": True}}, {"_id": 1})
                if ObjectId.is_valid(data["manager_id"]) else None
            )
            if not manager:
                raise BadRequestException("Pick a manager account for this center.")
        reshaped = False
        if any(field in data for field in _SLOT_SHAPE_FIELDS):
            center = await self.repo.find_by_id(center_id)
            if not center:
                raise NotFoundException("Service center not found")
            changed = {**center, **data}
            # A one-sided edit is checked against the stored other half
            # (closing at 08:00 a center that opens at 09:00).
            try:
                ensure_opens_before_closes(changed.get("working_hours_start"), changed.get("working_hours_end"))
            except ValueError as exc:
                raise BadRequestException(str(exc))
            from app.services.booking_service import BookingService

            blockers = await BookingService(self.db).slot_change_blockers(center, changed)
            if blockers:
                raise BadRequestException(
                    "Can't change this center's opening hours or slot length yet: " + "; ".join(blockers) + "."
                )
            reshaped = True
        updated = await self.repo.update_by_id(center_id, data)
        if not updated:
            raise NotFoundException("Service center not found")
        if reshaped:
            from app.services.capacity_policy_service import CapacityPolicyService

            # Nothing live sat in the old slots; the policy in effect today
            # is re-split over the new ones (a no-op when they didn't change).
            await CapacityPolicyService(self.db).rekey_for_current_slots(center_id)
        return serialize_doc(updated)

    async def delete(self, center_id: str) -> None:
        """Audit ADM-05: deleting a center that still has live bookings,
        staff, live passes or societies stranded all of them (no restore).
        Refused while anything depends on it — deactivate it instead (it
        then takes no new bookings and leaves every record readable)."""
        if not ObjectId.is_valid(center_id) or not await self.repo.find_by_id(center_id):
            raise NotFoundException("Service center not found")
        reasons = []
        bookings = await self.db.bookings.count_documents(
            {"service_center_id": center_id, "status": {"$in": _LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}
        )
        if bookings:
            reasons.append(f"{bookings} live booking{'s' if bookings != 1 else ''}")
        for role, label in (("captain", "captain"), ("manager", "manager")):
            n = await self.db.users.count_documents({"role": role, "service_center_id": center_id, "is_deleted": {"$ne": True}})
            if n:
                reasons.append(f"{n} {label}{'s' if n != 1 else ''}")
        passes = await self.db.user_subscriptions.count_documents(
            {"service_center_id": center_id, "status": {"$in": ["active", "scheduled"]}, "is_deleted": {"$ne": True}}
        )
        if passes:
            reasons.append(f"{passes} active pass{'es' if passes != 1 else ''}")
        societies = await self.db.societies.count_documents({"service_center_id": center_id, "is_deleted": {"$ne": True}})
        if societies:
            reasons.append(f"{societies} societ{'ies' if societies != 1 else 'y'}")
        if reasons:
            raise BadRequestException(
                f"This center can't be deleted while it still has {', '.join(reasons)}. "
                "Deactivate it instead (it stops taking new bookings), or move them to another center first."
            )
        deleted = await self.repo.soft_delete(center_id)
        if not deleted:
            raise NotFoundException("Service center not found")
