"""
Writes an immutable audit trail entry for sensitive actions performed
by staff (managers/admins). Every admin-facing mutation should call
`log_action` so the Audit Logs module in the PRD has real data.

Center attribution: every entry carries the service center the action
touched (`service_center_id`, top level, indexed) whenever it can be
known. An admin has no center of their own — when one works a center's
queue from the admin panel ("Manage this center's queue"), the entry must
still say WHICH center it acted in, so the center is resolved here from
the target record itself (the booking / visit / complaint / captain),
never from anything the client sent. A manager's own center comes from
their DB user record (CurrentUser), which the callers pass as-is.
"""
import logging

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.base import utcnow

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db["audit_logs"]

    async def _target_center(self, module: str, target_id: str | None) -> tuple[str | None, str | None]:
        """(service_center_id, human label) of the record an action touched,
        for the modules whose records belong to one center. Best effort —
        an unknown/foreign id simply yields (None, None)."""
        if not target_id:
            return None, None
        try:
            if module == "bookings":
                if ObjectId.is_valid(target_id):
                    b = await self.db.bookings.find_one({"_id": ObjectId(target_id)}, {"service_center_id": 1, "booking_number": 1})
                    if b:
                        return b.get("service_center_id"), b.get("booking_number")
                # Visit-level actions (group cancel/assign) target the group id.
                b = await self.db.bookings.find_one({"booking_group_id": target_id}, {"service_center_id": 1, "booking_number": 1})
                if b:
                    return b.get("service_center_id"), b.get("booking_number")
            elif module == "complaints" and ObjectId.is_valid(target_id):
                c = await self.db.complaints.find_one({"_id": ObjectId(target_id)}, {"service_center_id": 1, "ticket_number": 1})
                if c:
                    return c.get("service_center_id"), c.get("ticket_number")
            elif module in ("users", "staff", "captains", "kyc", "wallet") and ObjectId.is_valid(target_id):
                u = await self.db.users.find_one({"_id": ObjectId(target_id)}, {"service_center_id": 1, "role": 1})
                if u and u.get("role") in ("captain", "manager"):
                    return u.get("service_center_id"), None
            elif module == "service_centers" and ObjectId.is_valid(target_id):
                return target_id, None
        except Exception:  # noqa: BLE001 — attribution must never fail the action it records
            logger.warning("Could not resolve the center for audit target %s/%s", module, target_id, exc_info=True)
        return None, None

    async def log_action(
        self,
        actor_id: str,
        actor_role: str,
        action: str,
        module: str,
        target_id: str | None = None,
        details: dict | None = None,
        service_center_id: str | None = None,
    ) -> None:
        details = dict(details or {})
        center = service_center_id or details.get("service_center_id")
        label = None
        if not center or actor_role == "admin":
            resolved, label = await self._target_center(module, target_id)
            center = center or resolved
        # A manager / captain only ever acts inside their own center — when
        # the record itself doesn't say (an inventory item, a plan sale),
        # their DB user record does.
        if not center and actor_role in ("manager", "captain") and ObjectId.is_valid(actor_id or ""):
            actor = await self.db.users.find_one({"_id": ObjectId(actor_id)}, {"service_center_id": 1})
            center = (actor or {}).get("service_center_id")
        entry = {
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "module": module,
            "target_id": target_id,
            "details": details,
            "created_at": utcnow(),
        }
        if center:
            entry["service_center_id"] = center
        if label:
            entry["target_label"] = label
        # An admin acting on a center-owned record is acting "as" that
        # center's manager — flag it so the trail can be filtered for it.
        if actor_role == "admin" and center:
            entry["admin_in_center"] = True
        await self.collection.insert_one(entry)
