"""
Finance ledger (admin only) — the founder's simple credit/debit book for
money that is NOT a booking: salary, marketing, rent, product purchases and
product sales. Bookings stay in their own collections and KPIs; this module
never writes to them.

Storage: `finance_entries`, flat documents, integer PAISE, an IST calendar
day string (`entry_date`), UTC instants for audit times. An entry is never
hard-deleted: edits are audited (before/after) and a void is a soft delete
with a reason; every total ignores voided entries.

BOOKINGS EARNING (read-only reuse of PaymentService.admin_collections — the
admin Collections page's own number): money actually RECEIVED for
non-cancelled bookings (cash collected + paid online, wallet-credit spends
excluded) MINUS the paybacks managers made to customers —
`totals.net_collected`. Not the billed value. The window is the bookings'
scheduled day (exactly like the Collections page). Plan / society sales are
NOT in it (they have their own lines on that page); they are shown beside it
as `plan_sales_*` for information and are not added to the balance.
"""
import asyncio
import re
import time
from datetime import date, timedelta

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.base import utcnow
from app.repositories.finance_repository import FinanceRepository
from app.schemas.finance_schema import FinanceEntryCreate, FinanceEntryUpdate
from app.services.audit_service import AuditService, field_changes
from app.utils.csv_safe_finance import build_csv
from app.utils.timezone import now_ist

SUGGESTED_CATEGORIES = [
    "Salary", "Marketing", "Rent", "Product Purchase", "Product Sale", "Utilities", "Travel", "Equipment", "Repairs", "Other",
]
BOOKINGS_EARNING_DEFINITION = (
    "Money actually received for non-cancelled bookings (cash collected + paid online) minus what managers paid back "
    "to customers — the Collections page's net collected. Not the billed value; plan and society sales are separate."
)
MAX_FUTURE_DAYS = 1
MAX_PAST_DAYS = 5 * 365 + 1
MAX_RANGE_DAYS = 5 * 366 + 2
CSV_MAX_ROWS = 50_000
_BOOKINGS_CACHE_TTL = 60.0
_ALL_FROM, _ALL_TO = "2000-01-01", "2100-01-01"

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def clear_cache() -> None:
    _cache.clear()


def rupees(paise: int | None) -> float:
    return round((paise or 0) / 100, 2)


def _today() -> date:
    return now_ist().date()


def _parse_day(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequestException(f"'{name}' must be a date like 2026-10-28.") from None


def _check_entry_date(value: str) -> str:
    day = _parse_day(value, "Date")
    today = _today()
    if day > today + timedelta(days=MAX_FUTURE_DAYS):
        raise BadRequestException("The date can't be more than a day in the future.")
    if day < today - timedelta(days=MAX_PAST_DAYS):
        raise BadRequestException("The date can't be older than 5 years.")
    return day.isoformat()


def _public(doc: dict) -> dict:
    def iso(dt):
        return dt.isoformat() + "Z" if dt is not None and getattr(dt, "tzinfo", None) is None else (dt.isoformat() if dt else None)

    return {
        "id": str(doc["_id"]),
        "title": doc.get("title"),
        "type": doc.get("type"),
        "amount_paise": doc.get("amount_paise", 0),
        "amount_rupees": rupees(doc.get("amount_paise")),
        "entry_date": doc.get("entry_date"),
        "category": doc.get("category"),
        "description": doc.get("description") or "",
        "done_by": doc.get("done_by"),
        "reference": doc.get("reference"),
        "created_by": doc.get("created_by"),
        "created_at": iso(doc.get("created_at")),
        "updated_at": iso(doc.get("updated_at")),
        "is_deleted": bool(doc.get("is_deleted")),
        "deleted_by": doc.get("deleted_by"),
        "deleted_at": iso(doc.get("deleted_at")),
        "delete_reason": doc.get("delete_reason"),
    }


def _audit_view(doc: dict) -> dict:
    return {k: doc.get(k) for k in ("title", "type", "amount_paise", "entry_date", "category", "description", "done_by", "reference")}


class FinanceService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = FinanceRepository(db)
        self.col = self.repo.collection

    # ---------- filters ----------

    def _filters(self, *, type=None, category=None, date_from=None, date_to=None, q=None, done_by=None, include_voided=False) -> dict:
        f: dict = {}
        if not include_voided:
            f["is_deleted"] = False
        if type:
            f["type"] = type
        if category:
            f["category"] = {"$regex": f"^{re.escape(category.strip())}$", "$options": "i"}
        lo, hi = _parse_day(date_from, "from"), _parse_day(date_to, "to")
        if lo and hi and lo > hi:
            raise BadRequestException("'from' can't be after 'to'.")
        if lo or hi:
            rng: dict = {}
            if lo:
                rng["$gte"] = lo.isoformat()
            if hi:
                rng["$lte"] = hi.isoformat()
            f["entry_date"] = rng
        if done_by:
            f["done_by"] = {"$regex": f"^{re.escape(done_by.strip())}$", "$options": "i"}
        if q and q.strip():
            rx = {"$regex": re.escape(q.strip()[:100]), "$options": "i"}
            f["$or"] = [{"title": rx}, {"description": rx}, {"done_by": rx}, {"reference": rx}]
        return f

    # ---------- entries ----------

    async def list(self, page: int, page_size: int, **filters) -> tuple[list[dict], int]:
        flt = self._filters(**filters)
        total = await self.col.count_documents(flt)
        cursor = self.col.find(flt).sort([("entry_date", -1), ("created_at", -1), ("_id", -1)]).skip((page - 1) * page_size).limit(page_size)
        return [_public(d) for d in await cursor.to_list(length=page_size)], total

    async def create(self, payload: FinanceEntryCreate, actor_id: str, actor_role: str) -> dict:
        entry_date = _check_entry_date(payload.entry_date or _today().isoformat())
        doc = {
            "title": payload.title,
            "type": payload.type,
            "amount_paise": payload.amount_paise,
            "entry_date": entry_date,
            "category": payload.category,
            "description": payload.description or "",
            "done_by": payload.done_by,
            "reference": payload.reference,
            "created_by": actor_id,
            "deleted_by": None,
            "deleted_at": None,
            "delete_reason": None,
        }
        created = await self.repo.create(doc)
        await AuditService(self.db).log_action(
            actor_id, actor_role, "FINANCE_ENTRY_CREATE", "finance", str(created["_id"]), {"after": _audit_view(created)},
        )
        return _public(created)

    async def _get_live(self, entry_id: str) -> dict:
        if not ObjectId.is_valid(entry_id):
            raise NotFoundException("Entry not found")
        doc = await self.col.find_one({"_id": ObjectId(entry_id)})
        if not doc:
            raise NotFoundException("Entry not found")
        if doc.get("is_deleted"):
            raise ConflictException("This entry has been voided and can't be changed.")
        return doc

    async def update(self, entry_id: str, payload: FinanceEntryUpdate, actor_id: str, actor_role: str) -> dict:
        before = await self._get_live(entry_id)
        sent = payload.model_dump(exclude_unset=True)
        # Required fields can't be cleared; optional ones can (null).
        for key in ("title", "type", "amount_paise", "done_by"):
            if key in sent and sent[key] is None:
                raise BadRequestException(f"'{key}' can't be empty.")
        if "entry_date" in sent:
            if sent["entry_date"] is None:
                raise BadRequestException("The date can't be empty.")
            sent["entry_date"] = _check_entry_date(sent["entry_date"])
        if "description" in sent and sent["description"] is None:
            sent["description"] = ""
        changes = field_changes(before, {**before, **sent}, fields=list(sent))
        if not changes:
            return _public(before)
        after = await self.col.find_one_and_update(
            {"_id": before["_id"], "is_deleted": False},
            {"$set": {**{k: sent[k] for k in changes}, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if not after:
            raise ConflictException("This entry has been voided and can't be changed.")
        await AuditService(self.db).log_action(actor_id, actor_role, "FINANCE_ENTRY_UPDATE", "finance", entry_id, {"changes": changes})
        return _public(after)

    async def void(self, entry_id: str, reason: str, actor_id: str, actor_role: str) -> dict:
        before = await self._get_live(entry_id)
        now = utcnow()
        after = await self.col.find_one_and_update(
            {"_id": before["_id"], "is_deleted": False},
            {"$set": {"is_deleted": True, "deleted_by": actor_id, "deleted_at": now, "delete_reason": reason, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not after:
            raise ConflictException("This entry has already been voided.")
        await AuditService(self.db).log_action(
            actor_id, actor_role, "FINANCE_ENTRY_VOID", "finance", entry_id, {"reason": reason, "before": _audit_view(before)},
        )
        return _public(after)

    # ---------- categories ----------

    async def categories(self) -> dict:
        rows = await self.col.aggregate([
            {"$match": {"is_deleted": False, "category": {"$ne": None}}},
            {"$group": {"_id": "$category"}},
            {"$limit": 200},
        ]).to_list(length=200)
        used = sorted({r["_id"] for r in rows if r["_id"]}, key=str.lower)
        lower = {c.lower() for c in SUGGESTED_CATEGORIES}
        return {"suggested": SUGGESTED_CATEGORIES, "used": used, "all": SUGGESTED_CATEGORIES + [c for c in used if c.lower() not in lower]}

    # ---------- summary ----------

    async def _bookings_earning(self, date_from: str, date_to: str) -> dict:
        """Cached 60 s in-process (the Collections roll-up runs several
        aggregations; an all-time one is the heaviest)."""
        key = (date_from, date_to)
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _BOOKINGS_CACHE_TTL:
            return hit[1]
        lock = _locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < _BOOKINGS_CACHE_TTL:
                return hit[1]
            from app.services.payment_service import PaymentService  # local: keeps the import graph unchanged

            roll = await PaymentService(self.db).admin_collections(date_from, date_to)
            plans = sum(
                (roll.get(k) or {}).get("online_amount", 0) + (roll.get(k) or {}).get("cash_amount", 0)
                for k in ("subscriptions", "society", "custom_plans")
            )
            value = {
                "net_paise": int(round(float(roll["totals"].get("net_collected") or 0) * 100)),
                "plan_paise": int(round(float(plans) * 100)),
            }
            if len(_cache) > 64:
                _cache.clear()
            _cache[key] = (time.monotonic(), value)
            return value

    async def _sum_by_type(self, flt: dict) -> dict:
        rows = await self.col.aggregate([
            {"$match": flt}, {"$group": {"_id": "$type", "paise": {"$sum": "$amount_paise"}, "n": {"$sum": 1}}},
        ]).to_list(length=4)
        out = {"credit": 0, "debit": 0, "credit_n": 0, "debit_n": 0}
        for r in rows:
            if r["_id"] in ("credit", "debit"):
                out[r["_id"]] = int(r["paise"] or 0)
                out[f"{r['_id']}_n"] = r["n"]
        return out

    async def summary(self, date_from: str | None, date_to: str | None) -> dict:
        today = _today()
        lo = _parse_day(date_from, "from") or today.replace(day=1)
        hi = _parse_day(date_to, "to") or today
        if lo > hi:
            raise BadRequestException("'from' can't be after 'to'.")
        if (hi - lo).days > MAX_RANGE_DAYS:
            raise BadRequestException("Pick a range of at most 5 years.")
        lo_s, hi_s = lo.isoformat(), hi.isoformat()
        base = {"is_deleted": False}
        window = {**base, "entry_date": {"$gte": lo_s, "$lte": hi_s}}
        by_day = (hi - lo).days <= 62
        bucket = "$entry_date" if by_day else {"$substrBytes": ["$entry_date", 0, 7]}

        period_sums, all_sums, cats, series, book_period, book_all = await asyncio.gather(
            self._sum_by_type(window),
            self._sum_by_type(base),
            self.col.aggregate([
                {"$match": window},
                {"$group": {"_id": {"c": {"$ifNull": ["$category", None]}, "t": "$type"}, "paise": {"$sum": "$amount_paise"}}},
                {"$limit": 1000},
            ]).to_list(length=1000),
            self.col.aggregate([
                {"$match": window},
                {"$group": {"_id": {"k": bucket, "t": "$type"}, "paise": {"$sum": "$amount_paise"}}},
                {"$sort": {"_id.k": 1}},
                {"$limit": 2000},
            ]).to_list(length=2000),
            self._bookings_earning(lo_s, hi_s),
            self._bookings_earning(_ALL_FROM, _ALL_TO),
        )

        cat_map: dict[str, dict] = {}
        for r in cats:
            name = r["_id"]["c"] or "Uncategorised"
            row = cat_map.setdefault(name, {"category": name, "credit_paise": 0, "debit_paise": 0})
            row[f"{r['_id']['t']}_paise"] += int(r["paise"] or 0)
        by_category = []
        for row in sorted(cat_map.values(), key=lambda x: -(x["credit_paise"] + x["debit_paise"])):
            by_category.append({**row, "credit": rupees(row["credit_paise"]), "debit": rupees(row["debit_paise"])})

        ser_map: dict[str, dict] = {}
        for r in series:
            row = ser_map.setdefault(r["_id"]["k"], {"key": r["_id"]["k"], "credit_paise": 0, "debit_paise": 0})
            row[f"{r['_id']['t']}_paise"] += int(r["paise"] or 0)
        series_rows = [
            {**row, "credit": rupees(row["credit_paise"]), "debit": rupees(row["debit_paise"])} for _, row in sorted(ser_map.items())
        ]

        credits, debits = period_sums["credit"], period_sums["debit"]
        earning = book_period["net_paise"]
        balance = book_all["net_paise"] + all_sums["credit"] - all_sums["debit"]
        return {
            "period": {"from": lo_s, "to": hi_s},
            "bookings_earning_paise": earning, "bookings_earning": rupees(earning),
            "bookings_earning_definition": BOOKINGS_EARNING_DEFINITION,
            "credits_paise": credits, "credits": rupees(credits), "credit_count": period_sums["credit_n"],
            "debits_paise": debits, "debits": rupees(debits), "debit_count": period_sums["debit_n"],
            "net_other_paise": credits - debits, "net_other": rupees(credits - debits),
            "net_period_paise": earning + credits - debits, "net_period": rupees(earning + credits - debits),
            "total_balance_paise": balance, "total_balance": rupees(balance),
            "all_time": {
                "bookings_earning_paise": book_all["net_paise"], "bookings_earning": rupees(book_all["net_paise"]),
                "credits_paise": all_sums["credit"], "credits": rupees(all_sums["credit"]),
                "debits_paise": all_sums["debit"], "debits": rupees(all_sums["debit"]),
            },
            "plan_sales_paise": book_period["plan_paise"], "plan_sales": rupees(book_period["plan_paise"]),
            "plan_sales_note": "Subscription, society and custom-plan money received in the period. Shown for information; not added to the balance.",
            "by_category": by_category,
            "series_unit": "day" if by_day else "month",
            "series": series_rows,
        }

    # ---------- export ----------

    async def export_csv(self, actor_id: str, actor_role: str, **filters) -> str:
        flt = self._filters(**filters)
        cursor = self.col.find(flt).sort([("entry_date", -1), ("_id", -1)]).limit(CSV_MAX_ROWS)
        docs = await cursor.to_list(length=CSV_MAX_ROWS)
        rows = [
            [
                d.get("entry_date"), d.get("type"), f"{(d.get('amount_paise') or 0) / 100:.2f}", d.get("title"), d.get("category"),
                d.get("done_by"), d.get("description"), d.get("reference"), "Voided" if d.get("is_deleted") else "Active", d.get("delete_reason"),
            ]
            for d in docs
        ]
        await AuditService(self.db).log_action(
            actor_id, actor_role, "FINANCE_EXPORT", "finance", None,
            {"rows": len(rows), "filters": {k: v for k, v in filters.items() if v}},
        )
        return build_csv(
            ["Date", "Type", "Amount (INR)", "Name", "Category", "Done By", "Description", "Reference", "Status", "Void Reason"], rows,
        )
