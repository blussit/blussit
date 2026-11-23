"""Shared plumbing for the payroll services: business settings, captain
lookup with center scoping, actor snapshots, the ledger writer and the
finance / notification helpers. See PayrollService (ARCHITECTURE.md §7.13)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import NotificationType
from app.repositories.payroll_repository import PayrollRepository
from app.services import payroll_terms as pt
from app.utils.timezone import from_stored, now_ist

logger = logging.getLogger(__name__)

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"


def today_ist() -> str:
    return now_ist().date().isoformat()


def rupees_text(paise: int) -> str:
    """₹12,345 — whole rupees, Indian grouping, paise only when there are some."""
    paise = int(paise or 0)
    sign = "-" if paise < 0 else ""
    rupees, rest = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{sign}₹{digits}" + (f".{rest:02d}" if rest else "")


def iso(dt) -> str | None:
    if not dt:
        return None
    if isinstance(dt, datetime):
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).isoformat()
    return str(dt)


def ist_day(dt) -> str | None:
    """The IST day of a stored instant (completed_at …)."""
    if not isinstance(dt, datetime):
        return None
    return from_stored(dt).date().isoformat()


def split_by_weight(total: int, weights: list[int], caps: list[int] | None = None) -> list[int]:
    """Split whole paise in proportion to `weights`, adding up to exactly
    `total` (largest remainder), each share ≤ its cap when given."""
    n = len(weights)
    if n == 0:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        shares = [total // n] * n
        for i in range(total - sum(shares)):
            shares[i] += 1
    else:
        raw = [total * w / weight_sum for w in weights]
        shares = [int(x) for x in raw]
        order = sorted(range(n), key=lambda i: raw[i] - shares[i], reverse=True)
        for i in order[: total - sum(shares)]:
            shares[i] += 1
    if caps:
        shares = [min(s, c) for s, c in zip(shares, caps)]
    return shares


class PayrollBase:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = PayrollRepository(db)
        self.ledger = self.repo.collection
        self.users = db.users
        self.bookings = db.bookings

    # ---------- settings ----------

    async def settings(self, session=None) -> dict:
        doc = await self.db.business_settings.find_one({"_id": "singleton"}, session=session) or {}

        def num(key, default):
            value = doc.get(key)
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default

        return {
            "max_commission_percent": float(num("max_commission_percent", pt.DEFAULT_MAX_COMMISSION_PERCENT)),
            "payroll_alert_paise": int(num("payroll_alert_paise", pt.DEFAULT_PAYROLL_ALERT_PAISE)),
            "commission_alert_percent": float(num("commission_alert_percent", pt.DEFAULT_COMMISSION_ALERT_PERCENT)),
        }

    # ---------- captains ----------

    async def captain_or_404(self, captain_id: str, actor_role: str | None, actor_center_id: str | None, session=None) -> dict:
        """The captain, with the actor's center check: a manager reaches only
        their own center's captains (403 otherwise), an admin any. A manager
        or other non-captain id is a 400 — pay terms belong to captains."""
        user = None
        if ObjectId.is_valid(captain_id or ""):
            user = await self.users.find_one({"_id": ObjectId(captain_id), "is_deleted": {"$ne": True}}, session=session)
        if not user:
            raise NotFoundException("Captain not found")
        if user.get("role") != "captain":
            raise BadRequestException("Pay is only set for captains — a manager's own pay can't be edited here.")
        if actor_role is not None:
            ensure_own_center(actor_role, actor_center_id, user.get("service_center_id"))
        return user

    async def center_of(self, center_id: str | None, session=None) -> dict | None:
        if not center_id or not ObjectId.is_valid(center_id):
            return None
        return await self.db.service_centers.find_one({"_id": ObjectId(center_id)}, session=session)

    @staticmethod
    def operation_of(center: dict | None) -> dict:
        return ((center or {}).get("operation")) or {}

    async def finance_impact_for(self, center_id: str | None, session=None) -> bool:
        """False when the center's captains are paid by a franchise partner —
        nothing from this payment reaches Blussit Finance (D12)."""
        from app.services.franchise_service import captain_pay_finance_impact

        # One rule, shared with FranchiseService: effective-dated (a future-dated
        # switch does not apply yet) and only for a franchise-type center.
        return captain_pay_finance_impact(await self.center_of(center_id, session))

    async def actor_info(self, actor) -> dict:
        """{id, name, role} of the CurrentUser acting."""
        name = None
        if actor is not None and ObjectId.is_valid(actor.id or ""):
            row = await self.users.find_one({"_id": ObjectId(actor.id)}, {"full_name": 1})
            name = (row or {}).get("full_name")
        return {"id": getattr(actor, "id", None), "name": name or "System", "role": getattr(actor, "role", None) or "system"}

    # ---------- ledger ----------

    async def post(self, doc: dict, session=None) -> dict | None:
        """Insert one ledger row. None when its key already exists (the same
        post twice lands once)."""
        row = {"is_deleted": False, "created_at": datetime.now(timezone.utc), **doc}
        row.setdefault("finance_impact", False)
        row.setdefault("entry_date", today_ist())
        row["updated_at"] = row["created_at"]
        try:
            await self.ledger.insert_one(row, session=session)
        except DuplicateKeyError:
            if session is not None:
                raise  # inside a transaction the duplicate poisons it — the caller decides
            return None
        return row

    async def by_key(self, key: str, session=None) -> dict | None:
        return await self.ledger.find_one({"key": key}, session=session)

    # ---------- notifications / audit ----------

    async def admin_ids(self) -> list[str]:
        rows = await self.users.find(
            {"role": "admin", "status": {"$nin": ["suspended", "inactive"]}, "is_deleted": {"$ne": True}}, {"_id": 1},
        ).to_list(length=20)
        return [str(r["_id"]) for r in rows]

    async def notify(self, user_id: str | None, title: str, message: str, reference_id: str | None = None, whatsapp: bool = True) -> None:
        """In-app (and, for a captain, the generic WhatsApp update) — never
        allowed to fail the money write that triggered it."""
        if not user_id:
            return
        try:
            from app.services.notification_service import NotificationService

            await NotificationService(self.db).notify(
                user_id, title, message, NotificationType.SYSTEM, reference_id, send_whatsapp=whatsapp,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send the payroll notice %r to %s", title, user_id)

    async def notify_admins(self, title: str, message: str, reference_id: str | None = None) -> None:
        for admin_id in await self.admin_ids():
            await self.notify(admin_id, title, message, reference_id, whatsapp=False)

    async def audit(self, actor: dict, action: str, target_id: str | None, details: dict, service_center_id: str | None) -> None:
        try:
            from app.services.audit_service import AuditService

            await AuditService(self.db).log_action(
                actor.get("id") or "system", actor.get("role") or "system", action, "payroll", target_id, details,
                service_center_id=service_center_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not audit %s", action)
