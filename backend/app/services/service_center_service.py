from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.service_center_repository import STATUS_LABEL, ServiceCenterRepository, center_status
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest, ensure_opens_before_closes
from app.schemas.franchise_schema import SettlementCreate, today_ist
from app.services import franchise_service as fr
from app.services.audit_service import AuditService
from app.services.customer_wallet_service import in_transaction
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

    async def _decorate(self, centers: list[dict]) -> list[dict]:
        """Serialised centers with `operation` always present (a legacy one is
        self-operated) and the kit counts: `kits_assigned` (captains holding
        an unreturned kit), `captains_count`, and `kits_warning` — a warning
        only, never a block (H17)."""
        ids = [str(c["_id"]) for c in centers]
        kits = await fr.kits_assigned_map(self.db, ids)
        out = []
        for c in centers:
            row = serialize_doc(c)
            row["status"] = center_status(c)
            row["status_label"] = STATUS_LABEL[row["status"]]
            op = fr.operation_of(c)
            row["operation"] = op
            row["operation_history"] = row.get("operation_history") or []
            kit = kits.get(str(c["_id"]), {"kits_assigned": 0, "captains_count": 0})
            row.update(kit)
            row["kits_warning"] = fr.kit_warning(op, kit["kits_assigned"], kit["captains_count"])
            out.append(row)
        return out

    async def list_centers(self, page: int, page_size: int, search: str | None, active_only: bool = False, include_archived: bool = False):
        """`include_archived` (admin history views): archived centers too —
        their records stay readable, each row says active / paused / archived.
        `active_only` (pickers for new work) wins: paused and archived are out."""
        items, total = await self.repo.search(search, page, page_size, active_only, include_archived and not active_only)
        return await self._decorate(items), total

    async def get(self, center_id: str, redact: bool = False) -> dict:
        # An archived center still opens (its history screens need it).
        center = await self.repo.find_by_id(center_id, include_deleted=True)
        if not center:
            raise NotFoundException("Service center not found")
        row = (await self._decorate([center]))[0]
        return fr.redact_operation(row) if redact else row

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

    async def create(self, payload: ServiceCenterCreateRequest, actor=None) -> dict:
        doc = payload.model_dump(exclude={"operation"})
        # Stored explicitly: every lookup (nearest, pincode, bot, KPIs)
        # filters on it (QA 2026-10-07 — a new center was invisible).
        doc["is_active"] = True
        doc["code"] = self.repo.generate_code(payload.location.city)
        sent = payload.operation.model_dump(exclude_unset=True) if payload.operation else {}
        fee_now = bool(sent.get("franchise_fee_received_now"))
        fee_method = sent.get("franchise_fee_method") or "bank"
        operation, _ = fr.normalise_operation(None, sent)
        if fee_now and not (operation["type"] == "franchise" and operation["franchise_fee_paise"] > 0):
            raise BadRequestException("Only a franchise center with a franchise fee can have the fee marked as received.")
        # A new center starts with its operation (no history): it applies from today.
        operation["effective_from"] = today_ist().isoformat()
        doc["operation"] = operation
        doc["operation_history"] = []
        actor_id, actor_role = (getattr(actor, "id", None) or "system"), (getattr(actor, "role", None) or "system")

        async def _do(s):
            created = await self.repo.create(doc, session=s)
            if fee_now:
                # "Fee received now": the credit lands in Finance with the center, or neither does.
                await fr.FranchiseService(self.db).post_settlement(
                    str(created["_id"]),
                    SettlementCreate(kind="franchise_fee", amount_paise=operation["franchise_fee_paise"], method=fee_method if fee_method in ("cash", "bank", "upi", "other") else "bank",
                                     note="Franchise fee received when the center was added", idempotency_key="init-fee-0"),
                    actor_id, actor_role, session=s, center=created,
                )
            return created

        created = await in_transaction(self.db, _do, None)
        if operation["type"] == "franchise" and actor is not None:
            await AuditService(self.db).log_action(
                actor_id, actor_role, "FRANCHISE_CENTER_CREATED", "franchise", str(created["_id"]),
                {"after": fr.operation_snapshot(operation), "franchise_fee_paise": operation["franchise_fee_paise"], "fee_received_now": fee_now},
                service_center_id=str(created["_id"]),
            )
        return (await self._decorate([created]))[0]

    # Optional fields an admin can clear again (sent as null): unassigning
    # the manager stops that person's new-booking WhatsApps for this center.
    _CLEARABLE = ("manager_id", "contact_phone", "contact_email", "slot_duration_minutes")

    async def update(self, center_id: str, payload: ServiceCenterUpdateRequest, actor=None) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in self._CLEARABLE}
        op_sent = data.pop("operation", None) if "operation" in data else None
        history_entry = None
        before_op = None
        if op_sent:
            center = await self.repo.find_by_id(center_id)
            if not center:
                raise NotFoundException("Service center not found")
            before_op = fr.operation_of(center) if center.get("operation") else None
            fee_flag = op_sent.get("franchise_fee_received_now")
            if fee_flag or op_sent.get("franchise_fee_method"):
                raise BadRequestException("Record a franchise fee through a settlement (POST /franchise/centers/{id}/settlements).")
            clean, history_entry = fr.normalise_operation(center.get("operation") or None, op_sent)
            if history_entry:
                history_entry["by"] = {"id": getattr(actor, "id", None), "role": getattr(actor, "role", None)}
            data["operation"] = clean
        if data.get("manager_id"):
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
        if history_entry:
            # The operation and its history row are written together.
            data["updated_at"] = datetime.now(timezone.utc)
            res = await self.repo.collection.update_one(
                {"_id": ObjectId(center_id), "is_deleted": {"$ne": True}}, {"$set": data, "$push": {"operation_history": history_entry}},
            ) if ObjectId.is_valid(center_id) else None
            updated = await self.repo.find_by_id(center_id) if res and res.matched_count else None
        else:
            updated = await self.repo.update_by_id(center_id, data)
        if not updated:
            raise NotFoundException("Service center not found")
        if "operation" in data and actor is not None:
            await AuditService(self.db).log_action(
                getattr(actor, "id", None), getattr(actor, "role", None), "FRANCHISE_OPERATION_CHANGE" if history_entry else "FRANCHISE_OPERATION_EDIT",
                "franchise", center_id,
                {"before": fr.operation_snapshot(before_op or fr.DEFAULT_OPERATION), "after": fr.operation_snapshot(data["operation"]),
                 "effective_from": data["operation"].get("effective_from"), "changed": (history_entry or {}).get("changed", [])},
                service_center_id=center_id,
            )
        if reshaped:
            from app.services.capacity_policy_service import CapacityPolicyService

            # Nothing live sat in the old slots; the policy in effect today
            # is re-split over the new ones (a no-op when they didn't change).
            await CapacityPolicyService(self.db).rekey_for_current_slots(center_id)
        return (await self._decorate([updated]))[0]

    async def delete(self, center_id: str, actor=None) -> None:
        """ARCHIVE a center (the DELETE route). Nothing is removed: every
        booking, plan, ledger row, payroll / franchise record and audit entry
        that points at it stays readable, and it keeps showing as
        "Name (Archived)" wherever history is viewed. An archived center is
        also switched off (`is_active = false`), so it takes no new work, and
        it can be restored (`restore`). Refused while live bookings, staff,
        live passes or societies still depend on it (audit ADM-05) — pause it
        instead (it stops taking new bookings and every record stays)."""
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
                f"This center can't be archived while it still has {', '.join(reasons)}. "
                "Pause it instead (it stops taking new bookings and every record stays), or move them to another center first."
            )
        now = datetime.now(timezone.utc)
        res = await self.repo.collection.update_one(
            {"_id": ObjectId(center_id), "is_deleted": {"$ne": True}},
            {"$set": {"is_deleted": True, "is_active": False, "deleted_at": now, "archived_at": now,
                      "archived_by": getattr(actor, "id", None), "updated_at": now}},
        )
        if not res.modified_count:
            raise NotFoundException("Service center not found")

    async def restore(self, center_id: str, actor=None) -> dict:
        """Un-archive. The center comes back PAUSED — switching it on again
        is a separate, deliberate step."""
        if not ObjectId.is_valid(center_id):
            raise NotFoundException("Service center not found")
        now = datetime.now(timezone.utc)
        res = await self.repo.collection.update_one(
            {"_id": ObjectId(center_id), "is_deleted": True},
            {"$set": {"is_deleted": False, "is_active": False, "updated_at": now},
             "$unset": {"deleted_at": "", "archived_at": "", "archived_by": ""}},
        )
        if not res.matched_count:
            raise NotFoundException("This center isn't archived")
        return await self.get(center_id)
