"""Coupon bundles — single-use (or N-use) codes imported in bulk.

Model
-----
coupon_bundles   one document per bundle: name, benefit (the SAME fields a
                 coupon has), one expiry for the whole bundle, uses per code,
                 per-customer allowance, active flag. Editing a bundle is one
                 write here; no code document is touched.
coupon_codes     one document per code: `bundle_id`, the normalised `code`
                 (globally unique), `used_count`, `is_active`, added/used
                 stamps. Redemption only ever touches `used_count`.
coupon_usages    the same per-booking usage rows regular coupons write (keyed
                 `{code_id}:{booking_id}` for bundle codes).

Redemption, release and validation live in CouponService (the one place every
booking / plan / society path already goes through); this module is the
admin side: import, list, filter, export, edit, switch off, delete.

Status of a code (first match wins):
  Disabled   the bundle or the code is switched off
  Used       used_count >= the bundle's uses per code
  Expired    the bundle's expiry has passed (and it still had uses left)
  Partly Used  some, not all, of its uses spent
  Unused
"""
import logging
import re
from datetime import datetime, timezone
from typing import AsyncIterator

from bson import ObjectId
from fastapi import UploadFile
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import BulkWriteError

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.repositories.coupon_repository import CouponBundleRepository, CouponCodeRepository
from app.schemas.coupon_bundle_schema import BundleCreateRequest, BundleUpdateRequest
from app.services.audit_service import AuditService, field_changes
from app.services.coupon_rules import rules_of
from app.utils.coupon_codes import (
    EXISTS,
    FORMAT_HELP,
    SAMPLE_CSV,
    CsvFormatError,
    code_problem,
    decode_upload,
    normalise_code,
    parse_codes_csv,
)
from app.utils.csv_safe_finance import csv_safe_cell
from app.utils.timezone import from_stored, now_ist

logger = logging.getLogger(__name__)

STATUSES = ("unused", "partly_used", "used", "expired", "disabled")
STATUS_LABEL = {"unused": "Unused", "partly_used": "Partly Used", "used": "Used", "expired": "Expired", "disabled": "Disabled"}
_NONE = {"_id": {"$exists": False}}  # matches nothing

_ALLOWED_TYPES = {
    "text/csv", "application/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream", "text/x-csv", "",
}


def _iso(value: datetime | None) -> str | None:
    return from_stored(value).isoformat() if value else None


def _stamp(value: datetime | None) -> str:
    """12-hour IST, for the CSV export."""
    if not value:
        return ""
    d = from_stored(value)
    return d.strftime("%d %b %Y, ") + d.strftime("%I:%M %p").lstrip("0")


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(value or ""):
        raise NotFoundException("Bundle not found")
    return ObjectId(value)


def bundle_is_expired(bundle: dict, now: datetime | None = None) -> bool:
    until = bundle.get("valid_until")
    return bool(until) and (now or now_ist()) >= from_stored(until)


def classify(bundle: dict, code_active: bool, used: int, now: datetime | None = None) -> str:
    uses = int(bundle.get("uses_per_coupon") or 1)
    if not bundle.get("is_active", True) or not code_active:
        return "disabled"
    if used >= uses:
        return "used"
    if bundle_is_expired(bundle, now):
        return "expired"
    return "partly_used" if used > 0 else "unused"


def status_filter(bundle: dict, status: str, now: datetime | None = None) -> dict:
    """The Mongo filter (on coupon_codes) for one status of one bundle — the
    bundle-level facts (switched off, expired, uses per code) are constants,
    so each status is a plain indexed range on (is_active, used_count)."""
    uses = int(bundle.get("uses_per_coupon") or 1)
    b_active = bundle.get("is_active", True)
    expired = bundle_is_expired(bundle, now)
    active = {"is_active": {"$ne": False}}
    if status == "disabled":
        return {} if not b_active else {"is_active": False}
    if not b_active:
        return _NONE
    if status == "used":
        return {**active, "used_count": {"$gte": uses}}
    if status == "expired":
        return {**active, "used_count": {"$lt": uses}} if expired else _NONE
    if expired:
        return _NONE
    if status == "partly_used":
        return {**active, "used_count": {"$gt": 0, "$lt": uses}}
    if status == "unused":
        return {**active, "used_count": 0}
    raise BadRequestException("Unknown status filter")


class CouponBundleService:
    def __init__(self, db):
        self.db = db
        self.bundles = CouponBundleRepository(db)
        self.codes = CouponCodeRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------ upload

    @staticmethod
    async def read_upload(file: UploadFile) -> bytes:
        """The CSV bytes, refused beyond COUPON_BUNDLE_MAX_BYTES without ever
        holding more than limit+1 bytes."""
        name = (file.filename or "").lower()
        kind = (file.content_type or "").split(";")[0].strip().lower()
        if kind not in _ALLOWED_TYPES or not (name.endswith(".csv") or name.endswith(".txt") or kind.startswith("text/")):
            raise BadRequestException("Upload a .csv file. " + FORMAT_HELP)
        limit = settings.COUPON_BUNDLE_MAX_BYTES
        data = bytearray()
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise BadRequestException(f"That file is too large — the limit is {limit // (1024 * 1024)} MB.")
        if not data:
            raise BadRequestException("The file is empty. " + FORMAT_HELP)
        return bytes(data)

    async def _existing(self, codes: list[str]) -> set[str]:
        """Which of these codes already exist as a coupon or a bundle code."""
        found: set[str] = set()
        size = 2000
        for i in range(0, len(codes), size):
            chunk = codes[i:i + size]
            async for doc in self.codes.collection.find({"code": {"$in": chunk}}, {"code": 1}):
                found.add(doc["code"])
            async for doc in self.db.coupons.find({"code": {"$in": chunk}}, {"code": 1}):
                found.add(doc["code"])
        return found

    async def preview(self, raw: bytes) -> dict:
        """Parses the file and flags every row; nothing is written."""
        try:
            rows = parse_codes_csv(decode_upload(raw), settings.COUPON_BUNDLE_MAX_ROWS)
        except CsvFormatError as exc:
            raise BadRequestException(str(exc)) from exc
        usable = [r.code for r in rows if r.problem is None]
        existing = await self._existing(usable)
        out = []
        for r in rows:
            problem = r.problem or (EXISTS if r.code in existing else None)
            out.append([r.row, r.code, problem])
        bad = sum(1 for r in out if r[2])
        return {"rows": out, "total": len(out), "ok": len(out) - bad, "problems": bad, "format": FORMAT_HELP}

    @staticmethod
    def sample_csv() -> str:
        return SAMPLE_CSV

    # ------------------------------------------------------------ import

    async def _import_codes(self, bundle_id: str, raw_codes: list[str], actor_id: str) -> dict:
        """Adds the codes to a bundle in batches. Every code is re-checked
        here (the preview is advice, not trust): normalised, charset/length,
        duplicates within the request, then the unique index on `code` is the
        final word against anything that exists — or is being imported by a
        parallel request."""
        if len(raw_codes) > settings.COUPON_BUNDLE_MAX_ROWS:
            raise BadRequestException(f"At most {settings.COUPON_BUNDLE_MAX_ROWS:,} codes can be added at once.")
        invalid = 0
        duplicates = 0
        seen: set[str] = set()
        valid: list[str] = []
        for raw in raw_codes:
            code = normalise_code(raw)
            if code_problem(code):
                invalid += 1
            elif code in seen:
                duplicates += 1
            else:
                seen.add(code)
                valid.append(code)
        existing = await self._existing(valid)
        duplicates += len(existing)
        todo = [c for c in valid if c not in existing]
        now = datetime.now(timezone.utc)
        added = 0
        size = settings.COUPON_BUNDLE_BATCH_SIZE
        for i in range(0, len(todo), size):
            batch = [
                {"bundle_id": bundle_id, "code": c, "is_active": True, "used_count": 0, "last_used_at": None,
                 "last_use": None, "added_at": now, "added_by": actor_id, "is_deleted": False}
                for c in todo[i:i + size]
            ]
            try:
                await self.codes.collection.insert_many(batch, ordered=False)
                added += len(batch)
            except BulkWriteError as exc:
                errors = exc.details.get("writeErrors", [])
                if any(e.get("code") != 11000 for e in errors):
                    raise
                added += len(batch) - len(errors)
                duplicates += len(errors)  # lost a race to another import
        return {"added": added, "skipped_duplicates": duplicates, "invalid": invalid}

    async def create_bundle(self, payload: BundleCreateRequest, actor_id: str, actor_role: str) -> dict:
        if len(payload.codes) > settings.COUPON_BUNDLE_MAX_ROWS:
            raise BadRequestException(f"At most {settings.COUPON_BUNDLE_MAX_ROWS:,} codes can be added at once.")
        if await self.bundles.find_one({"name": payload.name}):
            raise ConflictException("A bundle with this name already exists")
        # Cheap pre-check so a hopeless request creates nothing.
        if not any(code_problem(normalise_code(c)) is None for c in payload.codes):
            raise BadRequestException("None of the selected codes can be added")
        now = datetime.now(timezone.utc)
        doc = payload.model_dump(exclude={"codes"})
        doc.update({
            "coupon_type": payload.coupon_type.value,
            "valid_from": now, "is_active": True, "created_by": actor_id, "code_total": 0,
        })
        bundle = await self.bundles.create(doc)
        bundle_id = str(bundle["_id"])
        try:
            summary = await self._import_codes(bundle_id, payload.codes, actor_id)
        except Exception:
            await self.codes.collection.delete_many({"bundle_id": bundle_id, "used_count": 0})
            await self.bundles.hard_delete(bundle_id)
            raise
        if summary["added"] == 0:
            await self.bundles.hard_delete(bundle_id)
            raise BadRequestException("None of the selected codes could be added — they already exist or are not valid")
        await self.audit.log_action(
            actor_id, actor_role, "CREATE_COUPON_BUNDLE", "coupons", bundle_id,
            {"name": payload.name, **summary, "uses_per_coupon": payload.uses_per_coupon},
        )
        return {"bundle": await self.get_bundle(bundle_id), **summary}

    async def add_codes(self, bundle_id: str, codes: list[str], actor_id: str, actor_role: str) -> dict:
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        summary = await self._import_codes(bundle_id, codes, actor_id)
        await self.audit.log_action(actor_id, actor_role, "ADD_COUPON_BUNDLE_CODES", "coupons", bundle_id, summary)
        return {"bundle": await self.get_bundle(bundle_id), **summary}

    # ------------------------------------------------------------ reads

    async def _counts(self, bundles: list[dict]) -> dict[str, dict]:
        """Status counts for several bundles in ONE aggregation over the
        (bundle_id, used_count, is_active) index."""
        ids = [str(b["_id"]) for b in bundles]
        by_id = {str(b["_id"]): b for b in bundles}
        now = now_ist()
        counts = {i: {"total": 0, "redemptions": 0, **{s: 0 for s in STATUSES}} for i in ids}
        if not ids:
            return counts
        pipeline = [
            {"$match": {"bundle_id": {"$in": ids}}},
            {"$group": {"_id": {"b": "$bundle_id", "a": "$is_active", "u": "$used_count"}, "n": {"$sum": 1}}},
        ]
        async for row in self.codes.collection.aggregate(pipeline):
            key = row["_id"]
            c = counts[key["b"]]
            n, used = int(row["n"]), int(key.get("u") or 0)
            c["total"] += n
            c["redemptions"] += n * used
            c[classify(by_id[key["b"]], key.get("a") is not False, used, now)] += n
        return counts

    def _view(self, bundle: dict, counts: dict) -> dict:
        now = now_ist()
        if not bundle.get("is_active", True):
            state = "Disabled"
        elif bundle_is_expired(bundle, now):
            state = "Expired"
        else:
            state = "Active"
        return {
            "id": str(bundle["_id"]),
            "name": bundle["name"],
            "description": bundle.get("description"),
            "offer_kind": bundle.get("offer_kind", "standard"),
            "coupon_type": bundle.get("coupon_type"),
            "value": bundle.get("value", 0),
            "min_order_value": bundle.get("min_order_value", 0),
            "max_discount_amount": bundle.get("max_discount_amount"),
            "eligible_service_keywords": bundle.get("eligible_service_keywords") or [],
            "free_addon_keywords": bundle.get("free_addon_keywords") or [],
            "uses_per_coupon": int(bundle.get("uses_per_coupon") or 1),
            "usage_limit_per_user": int(bundle.get("usage_limit_per_user") or 1),
            **rules_of(bundle),
            "valid_until": _iso(bundle.get("valid_until")),
            "is_active": bool(bundle.get("is_active", True)),
            "state": state,
            "created_at": _iso(bundle.get("created_at")),
            "counts": counts,
        }

    async def get_bundle(self, bundle_id: str) -> dict:
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        return self._view(bundle, (await self._counts([bundle]))[bundle_id])

    async def list_bundles(self, page: int, page_size: int, search: str | None) -> tuple[list[dict], int]:
        flt: dict = {}
        if search and search.strip():
            flt["name"] = {"$regex": re.escape(search.strip()), "$options": "i"}
        items, total = await self.bundles.find_many(flt, page=page, page_size=page_size)
        counts = await self._counts(items)
        return [self._view(b, counts[str(b["_id"])]) for b in items], total

    def _codes_query(
        self, bundle: dict, *, status: str | None, search: str | None, added_from: datetime | None, added_to: datetime | None,
        used_from: datetime | None, used_to: datetime | None,
    ) -> dict:
        clauses: list[dict] = [{"bundle_id": str(bundle["_id"])}]
        if status:
            if status not in STATUSES:
                raise BadRequestException("Unknown status filter")
            clauses.append(status_filter(bundle, status))
        if search and search.strip():
            clauses.append({"code": {"$regex": "^" + re.escape(normalise_code(search))}})
        for field, lo, hi in (("added_at", added_from, added_to), ("last_used_at", used_from, used_to)):
            rng: dict = {}
            if lo:
                rng["$gte"] = lo
            if hi:
                rng["$lt"] = hi
            if rng:
                clauses.append({field: rng})
        return {"$and": clauses} if len(clauses) > 1 else clauses[0]

    @staticmethod
    def _sort(sort_by: str, sort_order: int) -> list[tuple[str, int]]:
        direction = ASCENDING if sort_order == 1 else DESCENDING
        field = {"added": "added_at", "used": "last_used_at", "code": "code", "uses": "used_count"}.get(sort_by)
        if not field:
            raise BadRequestException("Unknown sort")
        return [(field, direction), ("_id", direction)]

    async def _decorate(self, bundle: dict, docs: list[dict]) -> list[dict]:
        """Code rows with who/where it was used — two batched lookups."""
        user_ids = {d["last_use"]["user_id"] for d in docs if d.get("last_use")}
        booking_ids = {d["last_use"]["booking_id"] for d in docs if d.get("last_use")}
        users = {
            str(u["_id"]): u
            async for u in self.db.users.find({"_id": {"$in": [ObjectId(i) for i in user_ids if ObjectId.is_valid(i)]}}, {"full_name": 1, "phone": 1})
        }
        bookings = {
            str(b["_id"]): b
            async for b in self.db.bookings.find({"_id": {"$in": [ObjectId(i) for i in booking_ids if ObjectId.is_valid(i)]}}, {"booking_number": 1})
        }
        uses = int(bundle.get("uses_per_coupon") or 1)
        now = now_ist()
        rows = []
        for d in docs:
            used = int(d.get("used_count") or 0)
            last = d.get("last_use") or None
            user = users.get(last["user_id"]) if last else None
            booking = bookings.get(last["booking_id"]) if last else None
            status = classify(bundle, d.get("is_active", True) is not False, used, now)
            rows.append({
                "id": str(d["_id"]), "code": d["code"], "status": status, "status_label": STATUS_LABEL[status],
                "is_active": d.get("is_active", True) is not False,
                "used_count": used, "uses_total": uses, "remaining": max(uses - used, 0),
                "added_at": _iso(d.get("added_at")), "used_at": _iso(d.get("last_used_at")),
                "used_by": {"id": last["user_id"], "name": (user or {}).get("full_name") or "Customer", "phone": (user or {}).get("phone")} if last else None,
                "booking_id": last["booking_id"] if last else None,
                "booking_number": (booking or {}).get("booking_number") if last else None,
            })
        return rows

    async def list_codes(
        self, bundle_id: str, page: int, page_size: int, *, status=None, search=None, sort_by="added", sort_order=-1,
        added_from=None, added_to=None, used_from=None, used_to=None,
    ) -> tuple[list[dict], int]:
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        query = self._codes_query(bundle, status=status, search=search, added_from=added_from, added_to=added_to, used_from=used_from, used_to=used_to)
        total = await self.codes.collection.count_documents(query)
        cursor = self.codes.collection.find(query).sort(self._sort(sort_by, sort_order)).skip((page - 1) * page_size).limit(page_size)
        return await self._decorate(bundle, await cursor.to_list(length=page_size)), total

    async def code_uses(self, bundle_id: str, code_id: str) -> list[dict]:
        """Every redemption of one code (a multi-use code has several)."""
        bundle = await self.bundles.find_by_id(bundle_id)
        code = await self.codes.find_by_id(code_id)
        if not bundle or not code or code.get("bundle_id") != bundle_id:
            raise NotFoundException("Code not found")
        usages = await self.db.coupon_usages.find({"coupon_id": code_id}).sort("created_at", -1).limit(500).to_list(length=500)
        users = {str(u["_id"]): u async for u in self.db.users.find({"_id": {"$in": [ObjectId(x["user_id"]) for x in usages if ObjectId.is_valid(x["user_id"])]}}, {"full_name": 1, "phone": 1})}
        bookings = {str(b["_id"]): b async for b in self.db.bookings.find({"_id": {"$in": [ObjectId(x["booking_id"]) for x in usages if ObjectId.is_valid(x["booking_id"])]}}, {"booking_number": 1})}
        return [
            {
                "used_at": _iso(x.get("created_at")), "booking_id": x["booking_id"],
                "booking_number": (bookings.get(x["booking_id"]) or {}).get("booking_number"),
                "user": {"id": x["user_id"], "name": (users.get(x["user_id"]) or {}).get("full_name") or "Customer", "phone": (users.get(x["user_id"]) or {}).get("phone")},
            }
            for x in usages
        ]

    async def export_csv(
        self, bundle_id: str, actor_id: str, actor_role: str, *, status=None, search=None, sort_by="added", sort_order=-1,
        added_from=None, added_to=None, used_from=None, used_to=None,
    ) -> AsyncIterator[str]:
        """The bundle (or the filtered part of it) as CSV text chunks: a
        cursor read 1,000 rows at a time, so a 100k-code bundle never sits
        in memory. Every cell is formula-neutralised."""
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        query = self._codes_query(bundle, status=status, search=search, added_from=added_from, added_to=added_to, used_from=used_from, used_to=used_to)
        sort = self._sort(sort_by, sort_order)
        await self.audit.log_action(actor_id, actor_role, "EXPORT_COUPON_BUNDLE", "coupons", bundle_id, {"status": status, "search": bool(search)})

        header = ["Code", "Status", "Times Used", "Uses Allowed", "Added On", "Last Used On", "Used By", "Phone", "Booking"]

        def line(cells) -> str:
            out = []
            for c in cells:
                text = csv_safe_cell(c)
                out.append('"' + text.replace('"', '""') + '"' if any(ch in text for ch in ',"\r\n') else text)
            return ",".join(out) + "\r\n"

        async def gen() -> AsyncIterator[str]:
            yield line(header)
            cursor = self.codes.collection.find(query).sort(sort).batch_size(1000)
            batch: list[dict] = []
            async for doc in cursor:
                batch.append(doc)
                if len(batch) >= 1000:
                    for r in await self._decorate(bundle, batch):
                        yield line(self._export_row(r))
                    batch = []
            if batch:
                for r in await self._decorate(bundle, batch):
                    yield line(self._export_row(r))

        return gen()

    @staticmethod
    def _export_row(r: dict) -> list:
        def when(iso):
            return _stamp(datetime.fromisoformat(iso)) if iso else ""
        who = r["used_by"]
        return [
            r["code"], r["status_label"], r["used_count"], r["uses_total"], when(r["added_at"]), when(r["used_at"]),
            who["name"] if who else "", (who or {}).get("phone") or "", r.get("booking_number") or "",
        ]

    # ------------------------------------------------------------ edit

    _CLEARABLE = ("description", "max_discount_amount", "valid_until", "time_window_start", "time_window_end")

    async def update_bundle(self, bundle_id: str, payload: BundleUpdateRequest, actor_id: str, actor_role: str) -> dict:
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        sent = payload.model_dump(exclude_unset=True)
        data = {k: v for k, v in sent.items() if v is not None or k in self._CLEARABLE}
        if "coupon_type" in data and data["coupon_type"] is not None:
            data["coupon_type"] = data["coupon_type"].value
        for key in ("eligible_service_keywords", "free_addon_keywords"):
            if key in data:
                data[key] = [v.strip().lower() for v in data[key] if v and v.strip()]
        if "name" in data and data["name"] != bundle["name"] and await self.bundles.find_one({"name": data["name"]}):
            raise ConflictException("A bundle with this name already exists")

        merged = {**bundle, **data}
        kind = merged.get("offer_kind") or "standard"
        if kind == "standard":
            value = float(merged.get("value") or 0)
            if value <= 0:
                raise BadRequestException("Standard coupons need a discount value")
            if merged.get("coupon_type") == "percentage" and value > 100:
                raise BadRequestException("A percentage discount can't be more than 100%")
        elif not merged.get("eligible_service_keywords") or not merged.get("free_addon_keywords"):
            raise BadRequestException("Free add-on offers need eligible service and free add-on keywords")

        # Uses per code can't drop below what a code has ALREADY had: lowering
        # it would make a used code "over-used" and quietly change history, so
        # the edit is refused (it never means "no further uses").
        if "uses_per_coupon" in data and data["uses_per_coupon"] < int(bundle.get("uses_per_coupon") or 1):
            new = data["uses_per_coupon"]
            over = await self.codes.collection.count_documents({"bundle_id": bundle_id, "used_count": {"$gt": new}})
            if over:
                top = await self.codes.collection.find_one({"bundle_id": bundle_id}, {"used_count": 1}, sort=[("used_count", -1)])
                raise ConflictException(
                    f"{over:,} code{'s have' if over != 1 else ' has'} already been used more than {new} time{'s' if new != 1 else ''} "
                    f"(the most used has been used {int((top or {}).get('used_count') or 0)}). Set uses per coupon to "
                    f"{int((top or {}).get('used_count') or 0)} or more, or switch the bundle off to stop further use."
                )
        start, end = merged.get("time_window_start"), merged.get("time_window_end")
        if bool(start) != bool(end) or (start and end and start >= end):
            raise BadRequestException("Give both a start and an end time for the time window, the end after the start")
        if not data:
            return await self.get_bundle(bundle_id)
        updated = await self.bundles.update_by_id(bundle_id, data)
        if not updated:
            raise NotFoundException("Bundle not found")
        await self.audit.log_action(
            actor_id, actor_role, "UPDATE_COUPON_BUNDLE", "coupons", bundle_id,
            {"name": bundle["name"], "changes": _json_safe(field_changes(bundle, updated, list(data)))},
        )
        return await self.get_bundle(bundle_id)

    async def set_code_active(self, bundle_id: str, code_id: str, active: bool, actor_id: str, actor_role: str) -> dict:
        code = await self.codes.find_by_id(code_id)
        if not code or code.get("bundle_id") != bundle_id or not await self.bundles.find_by_id(bundle_id):
            raise NotFoundException("Code not found")
        await self.codes.collection.update_one({"_id": code["_id"]}, {"$set": {"is_active": active}})
        await self.audit.log_action(
            actor_id, actor_role, "ENABLE_COUPON_CODE" if active else "DISABLE_COUPON_CODE", "coupons", bundle_id,
            {"code": code["code"][:3] + "***", "code_id": code_id},
        )
        return {"id": code_id, "is_active": active}

    async def delete_bundle(self, bundle_id: str, actor_id: str, actor_role: str) -> dict:
        """Nothing used yet: the bundle and its codes are deleted for good.
        Any code used: the bundle is only switched off and hidden — its codes
        and usage history stay (a booking may still hold one)."""
        bundle = await self.bundles.find_by_id(bundle_id)
        if not bundle:
            raise NotFoundException("Bundle not found")
        used = await self.codes.collection.count_documents({"bundle_id": bundle_id, "used_count": {"$gt": 0}})
        removed = 0
        if not used:
            # Only never-used codes go; one redeemed in the meantime stays.
            removed = (await self.codes.collection.delete_many({"bundle_id": bundle_id, "used_count": 0})).deleted_count
            if not await self.codes.collection.find_one({"bundle_id": bundle_id}, {"_id": 1}):
                await self.bundles.hard_delete(bundle_id)
                await self.audit.log_action(actor_id, actor_role, "DELETE_COUPON_BUNDLE", "coupons", bundle_id, {"name": bundle["name"], "mode": "deleted", "codes_removed": removed})
                return {"mode": "deleted", "codes_removed": removed}
        await self.bundles.collection.update_one(
            {"_id": bundle["_id"]},
            {"$set": {"is_deleted": True, "is_active": False, "deleted_at": datetime.now(timezone.utc), "updated_by": actor_id}},
        )
        await self.audit.log_action(actor_id, actor_role, "DISABLE_COUPON_BUNDLE", "coupons", bundle_id, {"name": bundle["name"], "mode": "disabled", "codes_removed": removed})
        return {"mode": "disabled", "codes_removed": removed}


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
