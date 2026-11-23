"""
Franchise service centers (ARCHITECTURE.md §7.13 — D18, D19, H14–H17).

A center is either self-operated or run by a franchise partner who keeps
`revenue_share_percent` of the *commission base* of every job completed there
(brochure: partner 70 %, Blussit 30 %). This module owns

  * the center's `operation` rules over time (`operation_as_of`) — the share /
    type / pay-manager in force on the day a job was completed;
  * share accrual (`sync_share`): per booking, a delta row in
    `franchise_ledger` keyed `fr:{booking}:{rev}` — the same delta / guarded
    revision pattern as MoneyService.settle_captain_wallet. Accrual is a
    liability, never cash: it does not touch Finance;
  * the settlement statement and settlements (money actually paid to / received
    from the partner, the franchise fee, kit sales), each posting a locked
    Finance entry in the same transaction;
  * the Finance summary's "Owed To Franchise Partners" lines.

Money is integer paise; days are IST `YYYY-MM-DD`.

Net position: payable = share accrued − cash the partner holds (their
captains collected it) − paid to the partner + received from the partner. Cash
a partner's captains collected is the partner's to hand over; online money is
Blussit's. Positive = Blussit owes the partner, negative = the partner owes
Blussit.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.base import utcnow
from app.repositories.service_center_repository import STATUS_LABEL, center_status
from app.schemas.franchise_schema import SettlementCreate, finalize_operation, today_ist
from app.services.audit_service import AuditService
from app.services.customer_wallet_service import in_transaction
from app.utils.timezone import IST, from_stored

logger = logging.getLogger(__name__)

SETTLEMENT_KINDS = ("pay_partner", "receive_from_partner", "franchise_fee", "kit_sale")
# franchise_ledger.kind per settlement kind (the ledger kinds are in §7.13 D).
_LEDGER_KIND = {
    "pay_partner": "settlement_pay",
    "receive_from_partner": "settlement_receive",
    "franchise_fee": "franchise_fee",
    "kit_sale": "kit_sale",
}
_SETTLEMENT_LEDGER_KINDS = tuple(_LEDGER_KIND.values())
# (finance type, category, finance source) per settlement kind.
_FINANCE = {
    "pay_partner": ("debit", "Franchise Payout", "franchise_settlement"),
    "receive_from_partner": ("credit", "Franchise Remittance", "franchise_settlement"),
    "franchise_fee": ("credit", "Franchise Fee", "franchise_fee"),
    "kit_sale": ("credit", "Kit Sale", "franchise_kit_sale"),
}
_KIND_LABEL = {
    "pay_partner": "Payout to partner",
    "receive_from_partner": "Remittance from partner",
    "franchise_fee": "Franchise fee",
    "kit_sale": "Kit sale",
}
_EARLIEST = "2000-01-01"
_LATEST = "2099-12-31"
_LEDGER_LIMIT = 300

DEFAULT_OPERATION = {
    "type": "self_operated",
    "partner": None,
    "revenue_share_percent": None,
    "franchise_fee_paise": 0,
    "kits_purchased": 0,
    "kits_given": 0,
    "captain_pay_managed_by": "blussit",
    "default_captain_pay": {"basis": None, "salary_monthly_paise": None, "commission_percent": None},
    "effective_from": None,
}


def rupees(paise: int | None) -> float:
    return round((paise or 0) / 100, 2)


def _to_paise(value) -> int:
    return int(round(float(value or 0) * 100))


def _mirror(row: dict, *keys: str) -> dict:
    """Add the rupee mirror (`x_paise` -> `x`) of the given paise keys."""
    for key in keys:
        row[key.removesuffix("_paise")] = rupees(row.get(key))
    return row


# --------------------------------------------------------------------------
# The operation over time (pure helpers, no I/O)
# --------------------------------------------------------------------------


def operation_of(center: dict | None) -> dict:
    """The center's stored operation with every field present (a legacy
    center with none is self-operated)."""
    stored = (center or {}).get("operation") or {}
    return {**DEFAULT_OPERATION, **stored, "default_captain_pay": {**DEFAULT_OPERATION["default_captain_pay"], **(stored.get("default_captain_pay") or {})}}


def operation_snapshot(op: dict) -> dict:
    """The part of the operation that the history tracks and bookings freeze."""
    return {
        "type": op.get("type") or "self_operated",
        "revenue_share_percent": op.get("revenue_share_percent"),
        "captain_pay_managed_by": op.get("captain_pay_managed_by") or "blussit",
        "partner_name": (op.get("partner") or {}).get("name"),
    }


def _day_start(day: str) -> datetime:
    """The first instant of an IST day (aware)."""
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)


def effective_instant(day: str | None, now: datetime | None = None) -> datetime:
    """The exact moment a new operation rule takes effect (aware, IST).
    No day, or TODAY, means NOW: a job completed earlier today stays under the
    old terms and only jobs completed at / after the switch use the new ones.
    Any other day (a future date, or a backdated one) means the START of that
    IST day."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    if not day or day == now.date().isoformat():
        return now
    return _day_start(day)


def _eff_at(h: dict) -> datetime:
    """When a history entry took effect: its stored instant, or (older rows
    that only kept the day) the start of that IST day."""
    at = h.get("effective_at")
    if isinstance(at, datetime):
        return from_stored(at)
    return _day_start(h.get("effective_from") or _EARLIEST)


def _history(center: dict) -> list[dict]:
    rows = [h for h in (center.get("operation_history") or []) if isinstance(h, dict)]
    # Oldest effective moment first; a stable sort keeps the order they were saved in.
    return sorted(rows, key=_eff_at)


def _rules(snapshot: dict | None) -> dict:
    keys = ("type", "revenue_share_percent", "captain_pay_managed_by")
    return {k: (snapshot or {})[k] for k in keys if k in (snapshot or {})}


def operation_as_of(center: dict | None, when: str | datetime | None = None) -> dict:
    """The operation in force at `when`, on top of the center's current
    details (partner contact, fee, kits, default pay).

    * a datetime — the exact moment (a booking's `completed_at`): the type,
      revenue share and pay-manager of the latest history entry whose
      effective MOMENT is at or before it. A job completed earlier on the day
      of a switch keeps the old terms;
    * a day string (or nothing = today) — the latest entry whose
      `effective_from` day is on or before that day (day-level answers:
      salary payments, labels)."""
    op = operation_of(center)
    hist = _history(center or {})
    if not hist:
        return op
    if isinstance(when, datetime):
        at = when.astimezone(IST) if when.tzinfo else from_stored(when)
        for h in reversed(hist):
            if at >= _eff_at(h):
                return {**op, **_rules(h.get("after"))}
        return {**op, **_rules(hist[0].get("before"))}
    day = when or today_ist().isoformat()
    for h in reversed(hist):
        if day >= (h.get("effective_from") or _EARLIEST):
            return {**op, **_rules(h.get("after"))}
    return {**op, **_rules(hist[0].get("before"))}


def captain_pay_finance_impact(center: dict | None, day: str | None = None) -> bool:
    """D12/H16: does a captain payment at this center reach Blussit Finance?
    False only when it is a franchise center whose captains are paid by the
    franchise partner (as of `day`, default today)."""
    op = operation_as_of(center, day)
    return not (op.get("type") == "franchise" and op.get("captain_pay_managed_by") == "franchise")


def franchise_windows(center: dict | None) -> list[dict]:
    """The times the center ran as a franchise, as [start_at, end_at) instants
    (aware, IST; end_at None = still is) with their inclusive IST day ranges.
    `start_at` None = from the center's first day (created as a franchise),
    when no instant needs applying. Cash is counted as the partner's only
    inside them — on the day of a switch only jobs completed on the franchise
    side of the switch moment count."""
    center = center or {}
    hist = _history(center)
    op = operation_of(center)
    created = center.get("created_at")
    start_default = from_stored(created).date().isoformat() if isinstance(created, datetime) else _EARLIEST

    def window(start_day, start_at, end_at):
        end_day = (end_at - timedelta(microseconds=1)).astimezone(IST).date().isoformat() if end_at else None
        return {"start_day": start_day, "start_at": start_at, "end_day": end_day, "end_at": end_at}

    if not hist:
        if op["type"] == "franchise":
            return [window(op.get("effective_from") or start_default, None, None)]
        return []
    windows: list[dict] = []
    open_from: tuple[str, datetime | None] | None = (start_default, None) if (hist[0].get("before") or {}).get("type") == "franchise" else None
    for h in hist:
        at = _eff_at(h)
        after_type = (h.get("after") or {}).get("type")
        if open_from is None and after_type == "franchise":
            open_from = (at.date().isoformat(), at)
        elif open_from is not None and after_type != "franchise":
            windows.append(window(open_from[0], open_from[1], at))
            open_from = None
    if open_from is not None:
        windows.append(window(open_from[0], open_from[1], None))
    return windows


def franchise_periods(center: dict | None) -> list[tuple[str, str | None]]:
    """Inclusive IST day ranges during which the center ran as a franchise
    (end None = still is)."""
    return [(w["start_day"], w["end_day"]) for w in franchise_windows(center)]


def is_or_was_franchise(center: dict | None) -> bool:
    return bool(franchise_periods(center))


def redact_operation(center: dict) -> dict:
    """What a manager may see of a center's operation: type, share %, who pays
    captains, default pay, kits and the partner's NAME — never the partner's
    phone / email, the fee or the history (admin only)."""
    out = dict(center)
    op = out.get("operation")
    if isinstance(op, dict):
        slim = {k: v for k, v in op.items() if k not in ("partner", "franchise_fee_paise")}
        partner = op.get("partner")
        if partner:
            slim["partner"] = {"name": partner.get("name")}
        out["operation"] = slim
    out.pop("operation_history", None)
    return out


def normalise_operation(stored: dict | None, sent: dict | None, *, today: str | None = None, now: datetime | None = None) -> tuple[dict, dict | None]:
    """Merge an edit onto the stored operation and validate the result.
    Returns (new operation, history entry or None). `sent` holds only the
    fields the admin sent (exclude_unset). A switch to self-operated clears
    the franchise-only fields unless they were sent (and then validation
    refuses them); a switch to franchise needs partner + share."""
    today = today or today_ist().isoformat()
    current = {**DEFAULT_OPERATION, **(stored or {})}
    sent = dict(sent or {})
    effective = sent.pop("effective_from", None)
    sent.pop("franchise_fee_received_now", None)
    sent.pop("franchise_fee_method", None)
    merged = {**current, **sent}
    if sent.get("type") == "self_operated" and current.get("type") == "franchise":
        for key in ("partner", "revenue_share_percent", "franchise_fee_paise", "kits_purchased"):
            if key not in sent:
                merged[key] = DEFAULT_OPERATION[key]
        if "captain_pay_managed_by" not in sent:
            merged["captain_pay_managed_by"] = "blussit"
    if sent.get("type") == "franchise" and current.get("type") != "franchise":
        # Kits given to a self-operated center become the franchise's purchase only if sent.
        if "kits_given" not in sent:
            merged["kits_given"] = 0
    if merged.get("partner") is not None and not isinstance(merged["partner"], dict):
        merged["partner"] = dict(merged["partner"])
    try:
        clean = finalize_operation(merged)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from None
    before, after = operation_snapshot(current), operation_snapshot(clean)
    tracked = [k for k in ("type", "revenue_share_percent", "captain_pay_managed_by") if before[k] != after[k]]
    entry = None
    clean["effective_from"] = (stored or {}).get("effective_from")
    if tracked:
        clean["effective_from"] = effective or today
        at = effective_instant(effective, now)
        entry = {
            "at": utcnow(),
            "effective_from": clean["effective_from"],
            # The exact moment (not just the day): a job completed earlier on
            # the switch day keeps the old terms.
            "effective_at": at.astimezone(timezone.utc),
            "changed": tracked,
            "before": before,
            "after": after,
        }
    return clean, entry


async def kits_assigned_map(db, center_ids: list[str]) -> dict[str, dict]:
    """{center_id: {kits_assigned, captains_count}} — captains of the center
    holding a kit that has not been returned (pay.kit.provided and no
    returned_on), and all its live captains. H17: a warning, never a block."""
    if not center_ids:
        return {}
    rows = await db.users.aggregate([
        {"$match": {"role": "captain", "is_deleted": {"$ne": True}, "service_center_id": {"$in": list(center_ids)}}},
        {"$group": {
            "_id": "$service_center_id",
            "captains": {"$sum": 1},
            "kits": {"$sum": {"$cond": [{"$and": [
                {"$eq": [{"$ifNull": ["$pay.kit.provided", False]}, True]},
                {"$eq": [{"$ifNull": ["$pay.kit.returned_on", None]}, None]},
            ]}, 1, 0]}},
        }},
    ]).to_list(length=len(center_ids) + 5)
    out = {cid: {"kits_assigned": 0, "captains_count": 0} for cid in center_ids}
    for r in rows:
        out[r["_id"]] = {"kits_assigned": int(r["kits"]), "captains_count": int(r["captains"])}
    return out


def kit_warning(op: dict, kits_assigned: int, captains: int) -> str | None:
    """"1 kit = 1 captain": the plain-English mismatch, or None."""
    owned = op.get("kits_purchased") if op.get("type") == "franchise" else op.get("kits_given")
    owned = int(owned or 0)
    label = "purchased" if op.get("type") == "franchise" else "given"
    if captains > owned:
        return f"{captains} captain{'s' if captains != 1 else ''} but only {owned} kit{'s' if owned != 1 else ''} {label}."
    if kits_assigned > owned:
        return f"{kits_assigned} kit{'s' if kits_assigned != 1 else ''} assigned but only {owned} {label}."
    return None


# --------------------------------------------------------------------------
# Commission base
# --------------------------------------------------------------------------


def _base_paise(booking: dict) -> int:
    """The franchise share's base — the SAME base as the captain commission
    (D4): service value after real discounts, a plan-covered wash at its list
    value, excluding tip, carried balance and the customer distance charge.
    `booking_money.commission_base_paise` (BACKEND A) is the one definition."""
    from app.services.booking_money import commission_base_paise

    return max(0, int(commission_base_paise(booking)))


def _share_of(base_paise: int, percent: float) -> int:
    return int((Decimal(base_paise) * Decimal(str(percent)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _completion_day(booking: dict) -> str:
    stamp = booking.get("completed_at") or booking.get("closed_at")
    if isinstance(stamp, datetime):
        return from_stored(stamp).date().isoformat()
    return today_ist().isoformat()


def _completion_at(booking: dict) -> datetime:
    """The moment the job was completed (IST-aware); now when it has no stamp yet."""
    stamp = booking.get("completed_at") or booking.get("closed_at")
    return from_stored(stamp) if isinstance(stamp, datetime) else datetime.now(IST)


def _counts_for_share(booking: dict) -> bool:
    """A job earns the partner's share only while it is completed, live and not refunded."""
    return (
        booking.get("status") == "completed"
        and not booking.get("is_deleted")
        and booking.get("payment_status") not in ("refunded", "refund_due")
    )


def _oid(value) -> ObjectId | None:
    value = str(value or "")
    return ObjectId(value) if ObjectId.is_valid(value) else None


def _month_bounds(month: str) -> tuple[str, str]:
    try:
        first = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise BadRequestException("Month must look like 2026-11.") from None
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def _utc_instant(at: datetime) -> datetime:
    """An aware instant as the naive UTC datetime Mongo stores."""
    return at.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_naive(day: str, *, end: bool = False) -> datetime:
    """The UTC instant (naive, as stored) of an IST day's start (or the next day's start)."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    if end:
        d += timedelta(days=1)
    return d.astimezone(timezone.utc).replace(tzinfo=None)


class FranchiseService:
    def __init__(self, db):
        self.db = db
        self.bookings = db.bookings
        self.ledger = db.franchise_ledger

    # ------------------------------------------------------------------
    # Share accrual
    # ------------------------------------------------------------------

    async def sync_share(self, booking: dict | str, session=None) -> None:
        """Bring the partner's share of one booking in step with its money.

        Only for jobs completed at a franchise center (the operation in force
        on the completion day). At the first completion the share % is frozen
        on the booking (`franchise_share_percent`, `franchise_center_id`);
        after that the target is round(base × percent) while the job is
        completed, live and not refunded, else 0. Only the difference from
        `franchise_posted_paise` is posted, as a `franchise_ledger` row keyed
        `fr:{booking}:{rev}`, guarded on `franchise_rev` (idempotent: a
        second call posts nothing; a concurrent writer is refused and the
        caller retries). Never touches Finance: accrual is not cash."""
        oid = _oid(booking.get("_id") or booking.get("id")) if isinstance(booking, dict) else _oid(booking)
        if oid is None:
            return

        async def _do(s):
            b = await self.bookings.find_one({"_id": oid}, session=s)
            if not b:
                return
            frozen = b.get("franchise_share_percent")
            live = _counts_for_share(b)
            if frozen is None and not live:
                return
            center_id = b.get("franchise_center_id") or b.get("service_center_id")
            if not center_id:
                return
            if frozen is None:
                center = await self.db.service_centers.find_one(
                    {"_id": _oid(center_id)}, {"operation": 1, "operation_history": 1, "created_at": 1}, session=s,
                ) if _oid(center_id) else None
                op = operation_as_of(center, _completion_at(b))
                if op.get("type") != "franchise" or not op.get("revenue_share_percent"):
                    return
                # A center created as a franchise (no history) is the partner's only from
                # its effective day — a job back-dated before that earns no share (the
                # cash windows already count it that way).
                if not _history(center or {}) and (op.get("effective_from") or "") > _completion_at(b).astimezone(IST).date().isoformat():
                    return
                frozen = float(op["revenue_share_percent"])
            base = _base_paise(b) if live else int(b.get("franchise_base_paise") or 0)
            target = _share_of(base, frozen) if live else 0
            posted = int(b.get("franchise_posted_paise") or 0)
            delta = target - posted
            fields = {
                "franchise_share_percent": float(frozen),
                "franchise_center_id": center_id,
                "franchise_base_paise": base,
                "franchise_share_paise": target,
            }
            unchanged = delta == 0 and all(b.get(k) == v for k, v in fields.items())
            if unchanged:
                return
            rev = int(b.get("franchise_rev") or 0) + (1 if delta else 0)
            done = await self.bookings.update_one(
                {"_id": oid, "franchise_rev": b.get("franchise_rev")},
                {"$set": {**fields, "franchise_posted_paise": target, "franchise_rev": rev, "updated_at": utcnow()}},
                session=s,
            )
            if not done.modified_count:
                raise BadRequestException("The booking just changed — try again.")
            if delta:
                number = b.get("booking_number")
                await self.ledger.insert_one({
                    "service_center_id": center_id,
                    "kind": "share" if posted == 0 and not b.get("franchise_rev") else "share_adjust",
                    "amount_paise": delta,
                    "booking_id": str(oid),
                    "booking_number": number,
                    "key": f"fr:{oid}:{rev}",
                    "share_percent": float(frozen),
                    "base_paise": base,
                    "note": (
                        f"Booking {number}: partner share ₹{rupees(target):,.2f} at {frozen:g} %"
                        if live else f"Booking {number}: share reversed (not a completed, paid job any more)"
                    ),
                    "actor": {"id": "system", "role": "system"},
                    "created_at": utcnow(),
                }, session=s)

        try:
            await in_transaction(self.db, _do, session)
        except BadRequestException:
            if session is not None:
                raise
            # A concurrent writer moved it first: the re-run sees its result.
            await in_transaction(self.db, _do, None)

    @staticmethod
    def captain_pay_finance_impact(center: dict | None, day: str | None = None) -> bool:
        """For BACKEND A (D12/H16): False when this is a franchise center whose
        captains are paid by the franchise partner — no finance entry is made."""
        return captain_pay_finance_impact(center, day)

    # ------------------------------------------------------------------
    # Reads: statement
    # ------------------------------------------------------------------

    async def _franchise_centers(self) -> list[dict]:
        # Paused and archived (soft-deleted) centers stay: their share, cash
        # and settlements are still owed / owing and must never vanish.
        return await self.db.service_centers.find({
            "$or": [{"operation.type": "franchise"}, {"operation_history.after.type": "franchise"}],
        }).sort("name", 1).to_list(length=500)

    async def _center(self, center_id: str) -> dict:
        # Any state — a final settlement with an archived center is normal.
        center = await self.db.service_centers.find_one({"_id": _oid(center_id)}) if _oid(center_id) else None
        if not center:
            raise NotFoundException("Service center not found")
        return center

    async def _cash_online(self, center: dict, lo: str | None = None, hi: str | None = None) -> dict:
        """Cash and online money collected at the center inside its franchise
        periods (clipped to [lo, hi]) — the same aggregation the Collections
        roll-up uses (`PaymentService._COLLECTION_GROUP`'s cash / online
        expressions), minus the cash its manager paid back to customers.
        The partner's captains hold the cash; Blussit's account holds the
        online money."""
        from app.services.payment_service import PaymentService

        pay = PaymentService(self.db)
        center_id = str(center["_id"])
        cash = online = back_cash = 0.0
        for w in franchise_windows(center):
            a = max(w["start_day"], lo or _EARLIEST)
            b = min(w["end_day"] or _LATEST, hi or _LATEST)
            if a > b:
                continue
            window = pay._collections_window(a, b)
            match: dict = {"service_center_id": center_id, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"}, "scheduled_date": window}
            # The days a switch happens on: only jobs completed on the franchise
            # side of the switch moment (a morning job stays the old terms').
            edges = []
            if w["start_at"] is not None and a == w["start_day"]:
                after_day = datetime.strptime(w["start_day"], "%Y-%m-%d") + timedelta(days=1)
                edges.append({"$or": [{"scheduled_date": {"$gte": after_day}}, {"completed_at": {"$gte": _utc_instant(w["start_at"])}}]})
            if w["end_at"] is not None and b == w["end_day"]:
                after_day = datetime.strptime(w["end_day"], "%Y-%m-%d") + timedelta(days=1)
                edges.append({"$or": [{"scheduled_date": {"$gte": after_day}}, {"completed_at": {"$lt": _utc_instant(w["end_at"])}}]})
            if edges:
                match = {"$and": [match, *edges]}
            rows = await self.bookings.aggregate([
                {"$match": match},
                {"$group": {"_id": None, "cash": {"$sum": pay._CASH}, "online": {"$sum": pay._ONLINE}}},
            ]).to_list(length=1)
            if rows:
                cash += float(rows[0].get("cash") or 0)
                online += float(rows[0].get("online") or 0)
            back = (await pay._paid_back(a, b, center_id)).get(center_id) or {}
            back_cash += float(back.get("cash_amount") or 0)
        cash_p, online_p, back_p = _to_paise(cash), _to_paise(online), _to_paise(back_cash)
        return {
            "cash_collected_paise": cash_p,
            "online_collected_paise": online_p,
            "paid_back_cash_paise": back_p,
            "cash_held_paise": max(0, cash_p - back_p),
        }

    async def _completed_totals(self, center_id: str, lo: str | None = None, hi: str | None = None) -> dict:
        """Jobs whose share is currently in force: count, base, share (by completion day)."""
        match: dict = {"franchise_center_id": center_id, "franchise_share_percent": {"$ne": None}}
        if lo or hi:
            match["completed_at"] = {"$gte": _utc_naive(lo or _EARLIEST), "$lt": _utc_naive(hi or _LATEST, end=True)}
        rows = await self.bookings.aggregate([
            {"$match": match},
            {"$group": {
                "_id": None,
                "count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$franchise_posted_paise", 0]}, 0]}, 1, 0]}},
                "base": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$franchise_posted_paise", 0]}, 0]}, {"$ifNull": ["$franchise_base_paise", 0]}, 0]}},
                "share": {"$sum": {"$ifNull": ["$franchise_posted_paise", 0]}},
            }},
        ]).to_list(length=1)
        r = rows[0] if rows else {}
        return {"bookings_completed": int(r.get("count") or 0), "base_paise": int(r.get("base") or 0), "share_accrued_paise": int(r.get("share") or 0)}

    async def _ledger_sums(self, center_ids: list[str] | None = None) -> dict[str, dict[str, int]]:
        match: dict = {"voided": {"$ne": True}}
        if center_ids is not None:
            match["service_center_id"] = {"$in": center_ids}
        rows = await self.ledger.aggregate([
            {"$match": match},
            {"$group": {"_id": {"c": "$service_center_id", "k": "$kind"}, "paise": {"$sum": "$amount_paise"}}},
        ]).to_list(length=5000)
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["_id"]["c"], {})[r["_id"]["k"]] = int(r["paise"] or 0)
        return out

    @staticmethod
    def _position(share: int, cash_held: int, sums: dict) -> dict:
        paid = sums.get("settlement_pay", 0)
        received = sums.get("settlement_receive", 0)
        net = share - cash_held - paid + received
        return {
            "settled_paid_paise": paid,
            "settled_received_paise": received,
            "net_payable_paise": net,
            "payable_paise": max(0, net),
            "receivable_paise": max(0, -net),
            "direction": "owed_to_partner" if net > 0 else "owed_by_partner" if net < 0 else "settled",
        }

    @staticmethod
    def _fee_view(op: dict, sums: dict) -> dict:
        agreed = int(op.get("franchise_fee_paise") or 0)
        received = sums.get("franchise_fee", 0)
        pending = max(0, agreed - received)
        status = "none" if not agreed else "received" if pending == 0 else "partial" if received else "pending"
        return {"franchise_fee_paise": agreed, "fee_received_paise": received, "fee_pending_paise": pending, "fee_status": status}

    def _center_head(self, center: dict, kit: dict) -> dict:
        op = operation_of(center)
        current = operation_as_of(center)
        return {
            "id": str(center["_id"]),
            "name": center.get("name"),
            "code": center.get("code"),
            "is_active": center.get("is_active", True),
            "is_deleted": bool(center.get("is_deleted")),
            "status": center_status(center),
            "status_label": STATUS_LABEL[center_status(center)],
            "type": current["type"],
            "partner": op.get("partner") or (self._former_partner(center)),
            "revenue_share_percent": current.get("revenue_share_percent"),
            "captain_pay_managed_by": current.get("captain_pay_managed_by"),
            "effective_from": op.get("effective_from"),
            "kits_purchased": op.get("kits_purchased", 0),
            "kits_assigned": kit["kits_assigned"],
            "captains_count": kit["captains_count"],
            "kits_warning": kit_warning(current, kit["kits_assigned"], kit["captains_count"]) if current["type"] == "franchise" else None,
        }

    @staticmethod
    def _former_partner(center: dict) -> dict | None:
        """A center that is no longer a franchise keeps showing who its partner was."""
        for h in _history(center):
            name = (h.get("before") or {}).get("partner_name")
            if name:
                return {"name": name, "phone": None, "email": None}
        return None

    async def list_centers(self) -> dict:
        """Every franchise center (admin): share, accrued, settled, kits vs
        captains, fee status and the net position."""
        centers = await self._franchise_centers()
        ids = [str(c["_id"]) for c in centers]
        sums, kits = await asyncio.gather(self._ledger_sums(ids), kits_assigned_map(self.db, ids))
        cash, totals = await asyncio.gather(
            asyncio.gather(*[self._cash_online(c) for c in centers]),
            asyncio.gather(*[self._completed_totals(i) for i in ids]),
        )
        items = []
        for c, cid, cash_row, done in zip(centers, ids, cash, totals):
            s = sums.get(cid, {})
            op = operation_of(c)
            row = {
                **self._center_head(c, kits[cid]),
                **done,
                **cash_row,
                "share_accrued_paise": s.get("share", 0) + s.get("share_adjust", 0),
                **self._fee_view(op, s),
                "kit_sales_paise": s.get("kit_sale", 0),
            }
            row.update(self._position(row["share_accrued_paise"], cash_row["cash_held_paise"], s))
            _mirror(row, "base_paise", "share_accrued_paise", "cash_collected_paise", "online_collected_paise", "paid_back_cash_paise",
                    "cash_held_paise", "settled_paid_paise", "settled_received_paise", "net_payable_paise", "payable_paise",
                    "receivable_paise", "franchise_fee_paise", "fee_received_paise", "fee_pending_paise", "kit_sales_paise")
            items.append(row)
        totals_row = {
            "payable_paise": sum(r["payable_paise"] for r in items),
            "receivable_paise": sum(r["receivable_paise"] for r in items),
            "share_accrued_paise": sum(r["share_accrued_paise"] for r in items),
            "fee_pending_paise": sum(r["fee_pending_paise"] for r in items),
        }
        return {"items": items, "totals": _mirror(totals_row, "payable_paise", "receivable_paise", "share_accrued_paise", "fee_pending_paise")}

    async def owed_totals(self) -> dict:
        """The Finance summary's lines: what Blussit owes franchise partners
        (sum of positive nets) and what partners owe Blussit."""
        try:
            data = await self.list_centers()
            return {"franchise_payable_paise": data["totals"]["payable_paise"], "franchise_receivable_paise": data["totals"]["receivable_paise"]}
        except Exception:  # noqa: BLE001 — the Finance page must never break on this line
            logger.exception("Could not work out the franchise payable lines")
            return {"franchise_payable_paise": 0, "franchise_receivable_paise": 0}

    async def statement(self, center_id: str, month: str | None = None) -> dict:
        center = await self._center(center_id)
        if not is_or_was_franchise(center):
            raise BadRequestException("This is not a franchise center.")
        kit = (await kits_assigned_map(self.db, [center_id]))[center_id]
        sums = (await self._ledger_sums([center_id])).get(center_id, {})
        op = operation_of(center)
        lo = hi = None
        if month:
            lo, hi = _month_bounds(month)
        all_cash, all_done = await asyncio.gather(self._cash_online(center), self._completed_totals(center_id))
        all_time = {**all_done, **all_cash, "share_accrued_paise": sums.get("share", 0) + sums.get("share_adjust", 0)}
        all_time.update(self._position(all_time["share_accrued_paise"], all_cash["cash_held_paise"], sums))
        _mirror(all_time, "base_paise", "share_accrued_paise", "cash_collected_paise", "online_collected_paise", "paid_back_cash_paise",
                "cash_held_paise", "settled_paid_paise", "settled_received_paise", "net_payable_paise", "payable_paise", "receivable_paise")
        out = {
            "center": self._center_head(center, kit),
            "month": month,
            "all_time": all_time,
            "fee": self._fee_view(op, sums),
            "kit_sales_paise": sums.get("kit_sale", 0),
        }
        if month:
            m_cash, m_done = await asyncio.gather(self._cash_online(center, lo, hi), self._completed_totals(center_id, lo, hi))
            m = {**m_done, **m_cash, "from": lo, "to": hi}
            m["net_paise"] = m["share_accrued_paise"] - m["cash_held_paise"]
            _mirror(m, "base_paise", "share_accrued_paise", "cash_collected_paise", "online_collected_paise", "paid_back_cash_paise", "cash_held_paise", "net_paise")
            out["month_view"] = m
        settle_q: dict = {"service_center_id": center_id, "kind": {"$in": list(_SETTLEMENT_LEDGER_KINDS)}}
        ledger_q: dict = {"service_center_id": center_id}
        if month:
            window = {"$gte": _utc_naive(lo), "$lt": _utc_naive(hi, end=True)}
            ledger_q["created_at"] = window
        settlements, ledger = await asyncio.gather(
            self.ledger.find(settle_q).sort("created_at", -1).to_list(length=_LEDGER_LIMIT),
            self.ledger.find(ledger_q).sort("created_at", -1).to_list(length=_LEDGER_LIMIT),
        )
        out["settlements"] = [self._row(r) for r in settlements]
        out["ledger"] = [self._row(r) for r in ledger]
        return out

    @staticmethod
    def _row(r: dict) -> dict:
        row = {
            "id": str(r["_id"]),
            "kind": r["kind"],
            "settlement_kind": next((k for k, v in _LEDGER_KIND.items() if v == r["kind"]), None),
            "amount_paise": int(r.get("amount_paise") or 0),
            "booking_id": r.get("booking_id"),
            "booking_number": r.get("booking_number"),
            "method": r.get("method"),
            "paid_on": r.get("paid_on"),
            "note": r.get("note", ""),
            "voided": bool(r.get("voided")),
            "void_reason": r.get("void_reason"),
            "finance_entry_id": r.get("finance_entry_id"),
            "actor": r.get("actor"),
            "created_at": r["created_at"].replace(tzinfo=timezone.utc).isoformat() if isinstance(r.get("created_at"), datetime) else None,
        }
        return _mirror(row, "amount_paise")

    # ------------------------------------------------------------------
    # Settlements
    # ------------------------------------------------------------------

    async def _actor(self, actor_id: str, actor_role: str) -> dict:
        name = None
        if _oid(actor_id):
            u = await self.db.users.find_one({"_id": _oid(actor_id)}, {"full_name": 1})
            name = (u or {}).get("full_name")
        return {"id": actor_id, "role": actor_role, "name": name or "Admin"}

    async def post_settlement(self, center_id: str, payload: SettlementCreate, actor_id: str, actor_role: str = "admin", *, session=None, center: dict | None = None) -> dict:
        """One transaction: a `franchise_ledger` row + the locked Finance
        entry (pay partner = debit "Franchise Payout", receive = credit
        "Franchise Remittance", fee = credit "Franchise Fee", kit sale =
        credit "Kit Sale"). Idempotent on `idempotency_key`."""
        from app.services.finance_service import FinanceService

        center = center or await self._center(center_id)
        if not is_or_was_franchise(center):
            raise BadRequestException("This is not a franchise center.")
        op = operation_of(center)
        key = f"set:{center_id}:{payload.idempotency_key}" if payload.idempotency_key else f"set:{center_id}:{uuid.uuid4().hex}"
        existing = await self.ledger.find_one({"key": key}, session=session)
        if existing:
            return self._replay(existing, payload)
        if payload.kind == "franchise_fee":
            sums = (await self._ledger_sums([center_id])).get(center_id, {})
            agreed = int(op.get("franchise_fee_paise") or 0)
            if not agreed:
                raise BadRequestException("This center has no franchise fee set.")
            pending = agreed - sums.get("franchise_fee", 0)
            if payload.amount_paise > pending:
                raise BadRequestException(f"That is more than the franchise fee still pending (₹{rupees(max(0, pending)):,.2f}).")
        actor = await self._actor(actor_id, actor_role)
        row_id = ObjectId()
        ftype, category, source = _FINANCE[payload.kind]
        paid_on = payload.paid_on or today_ist().isoformat()
        label = _KIND_LABEL[payload.kind]
        row = {
            "_id": row_id,
            "service_center_id": center_id,
            "kind": _LEDGER_KIND[payload.kind],
            "amount_paise": payload.amount_paise,
            "method": payload.method,
            "paid_on": paid_on,
            "note": payload.note,
            "key": key,
            "voided": False,
            "actor": actor,
            "created_at": utcnow(),
        }

        async def _do(s):
            entry = await FinanceService(self.db).create_system_entry(
                title=f"{label} — {center.get('name')}", type=ftype, amount_paise=payload.amount_paise, category=category,
                source=source, source_id=f"{center_id}:{row_id}", service_center_id=center_id, done_by=actor["name"],
                actor_id=actor_id, actor_role=actor_role, entry_date=paid_on,
                description=(payload.note or f"{label} ₹{rupees(payload.amount_paise):,.2f} via {payload.method}"), session=s,
            )
            await self.ledger.insert_one({**row, "finance_entry_id": entry.get("id")}, session=s)
            return entry

        try:
            await in_transaction(self.db, _do, session)
        except DuplicateKeyError:
            dup = await self.ledger.find_one({"key": key})
            if dup:
                return self._replay(dup, payload)
            raise
        await AuditService(self.db).log_action(
            actor_id, actor_role, "FRANCHISE_SETTLEMENT", "franchise", center_id,
            {"kind": payload.kind, "amount_paise": payload.amount_paise, "method": payload.method, "paid_on": paid_on, "settlement_id": str(row_id)},
            service_center_id=center_id,
        )
        saved = await self.ledger.find_one({"_id": row_id}, session=session)
        return {**self._row(saved), "replayed": False}

    def _replay(self, existing: dict, payload: SettlementCreate) -> dict:
        if existing.get("kind") != _LEDGER_KIND[payload.kind] or int(existing.get("amount_paise") or 0) != payload.amount_paise:
            raise ConflictException("That idempotency key was already used for a different settlement.")
        return {**self._row(existing), "replayed": True}

    async def void_settlement(self, center_id: str, settlement_id: str, reason: str, actor_id: str, actor_role: str = "admin") -> dict:
        """Void a settlement (never deleted): the row stays, marked voided, and
        its Finance entry is voided in the same transaction. Idempotent."""
        from app.services.finance_service import FinanceService

        await self._center(center_id)
        oid = _oid(settlement_id)
        row = await self.ledger.find_one({"_id": oid, "service_center_id": center_id}) if oid else None
        if not row or row.get("kind") not in _SETTLEMENT_LEDGER_KINDS:
            raise NotFoundException("Settlement not found")
        if row.get("voided"):
            return self._row(row)
        kind = next(k for k, v in _LEDGER_KIND.items() if v == row["kind"])
        source = _FINANCE[kind][2]

        async def _do(s):
            done = await self.ledger.update_one(
                {"_id": oid, "voided": {"$ne": True}},
                {"$set": {"voided": True, "voided_at": utcnow(), "voided_by": actor_id, "void_reason": reason}}, session=s,
            )
            if done.modified_count:
                await FinanceService(self.db).void_system_entry(source, f"{center_id}:{oid}", f"Franchise settlement voided: {reason}", actor_id, actor_role, session=s)
            return bool(done.modified_count)

        changed = await in_transaction(self.db, _do, None)
        if changed:
            await AuditService(self.db).log_action(
                actor_id, actor_role, "FRANCHISE_SETTLEMENT_VOID", "franchise", center_id,
                {"settlement_id": settlement_id, "kind": kind, "amount_paise": row.get("amount_paise"), "reason": reason},
                service_center_id=center_id,
            )
        return self._row(await self.ledger.find_one({"_id": oid}))


# --------------------------------------------------------------------------
# Boot backfill + indexes
# --------------------------------------------------------------------------


async def backfill_center_operation(db) -> int:
    """Idempotent: a center with no `operation` becomes self-operated (the
    defaults). Only fills what is missing; never rewrites an existing one."""
    n = 0
    async for c in db.service_centers.find({"operation": {"$exists": False}}, {"created_at": 1}):
        created = c.get("created_at")
        since = from_stored(created).date().isoformat() if isinstance(created, datetime) else None
        res = await db.service_centers.update_one(
            {"_id": c["_id"], "operation": {"$exists": False}},
            {"$set": {"operation": {**DEFAULT_OPERATION, "effective_from": since}, "operation_history": []}},
        )
        n += res.modified_count
    return n


async def ensure_franchise_indexes(db) -> None:
    l = db.franchise_ledger
    await l.create_index([("key", 1)], unique=True, name="uniq_franchise_key")
    await l.create_index([("service_center_id", 1), ("created_at", -1)], name="center_created")
    await l.create_index([("booking_id", 1)], name="booking", sparse=True)
    await db.bookings.create_index(
        [("franchise_center_id", 1), ("completed_at", -1)], name="franchise_center_completed",
        partialFilterExpression={"franchise_center_id": {"$type": "string"}},
    )
