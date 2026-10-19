"""
Holidays: an admin declares a break for every service center, or for a
chosen few, for a day or a stretch of time. While it runs nobody can book
a wash in those hours, and the website shows a friendly popup from a few
days before ("We're not able to serve on 1 Oct – 2 Oct. You can book from
3 Oct 2026.").

One check, reused everywhere (`ensure_bookable` / `blocking_holiday`):
BookingService calls it from `_reserve_slot_capacity` (every path that
takes a seat — create, quick, group, staff create, reschedule, customer
edit, the WhatsApp bot, society premium washes), `hold_slot`,
`precheck_quick_booking`, and marks the slots in `available_slots`. A
manager logging a job already done never takes a seat, so it is never
blocked. Existing bookings inside a new holiday are NOT cancelled — the
admin sees them (`affected_bookings`) and acts.

The live holiday list (active, not yet ended) is cached per process for
30 s and dropped on every write here, so the booking hot path costs no
extra database round trip. Another API instance sees a change within 30 s.

Times: the admin types IST dates (+ optional times); they are stored as
UTC instants (`start_at`, `end_at`, `notice_from`) next to the typed
values. A holiday without an end time runs to 23:59:59.999 IST of its last
day; booking then reopens at 00:00 IST the next day (`resume_at`).
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.holiday_repository import HolidayRepository
from app.repositories.service_center_repository import ACTIVE_CENTER
from app.utils.slots import format_slot_12h, format_time_12h
from app.utils.timezone import IST

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30
MAX_LENGTH_DAYS = 60
MAX_DAYS_AHEAD = 365
DEFAULT_NOTICE_DAYS = 2
AFFECTED_SAMPLE_SIZE = 10
AFFECTED_COUNT_CAP = 5000
ACTIVE_BOOKING_STATUSES = ("awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled")
DAY_END = time(23, 59, 59, 999000)


class HolidayException(BadRequestException):
    """A booking/hold refused because the slot falls inside a holiday —
    its own error_code so every client can tell it from "slot full"."""

    error_code = "HOLIDAY"


# ---------------------------------------------------------------- cache

_cache: dict[str, tuple[float, list[dict]]] = {}
_generation = 0


def invalidate_holiday_cache() -> None:
    """Drop the cached live list (every write here calls it; tests that
    write holidays directly call it too)."""
    global _generation
    _generation += 1
    _cache.clear()


# ---------------------------------------------------------------- pure helpers


def _utc(value: datetime | None) -> datetime | None:
    """Stored instants come back naive (UTC) from this driver."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _normalize(doc: dict) -> dict:
    out = dict(doc)
    for key in ("start_at", "end_at", "notice_from", "created_at", "updated_at", "cancelled_at", "original_end_at"):
        if isinstance(out.get(key), datetime):
            out[key] = _utc(out[key])
    out["service_center_ids"] = [str(x) for x in out.get("service_center_ids") or []]
    out["scope"] = out.get("scope") or "all"
    return out


def applies_to(holiday: dict, center_id: str | None) -> bool:
    """A holiday covers a center when it is for every center, or lists it."""
    if holiday.get("scope") == "all":
        return True
    return bool(center_id) and str(center_id) in (holiday.get("service_center_ids") or [])


def overlaps(holiday: dict, start: datetime, end: datetime) -> bool:
    """[start, end) meets the holiday [start_at, end_at)."""
    return holiday["start_at"] < end and start < holiday["end_at"]


def _is_day_end(moment: datetime) -> bool:
    m = moment.astimezone(IST)
    return (m.hour, m.minute, m.second) == (23, 59, 59)


def resume_point(end_at: datetime) -> datetime:
    """The first bookable moment after one holiday (IST): 00:00 the next
    day when it ran to the end of its last day, else its end time."""
    end = end_at.astimezone(IST)
    if _is_day_end(end):
        return end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return end


def _chain_pool(holiday: dict, pool: list[dict], center_id: str | None) -> list[dict]:
    """The other holidays that can extend this one's break for whoever is
    asking: a known center → everything covering it; no center → for an
    all-centers holiday only other all-centers ones, for a centers holiday
    the ones covering every center it lists."""
    if center_id:
        return [h for h in pool if applies_to(h, center_id)]
    if holiday.get("scope") == "all":
        return [h for h in pool if h.get("scope") == "all"]
    ids = set(holiday.get("service_center_ids") or [])
    return [h for h in pool if h.get("scope") == "all" or ids <= set(h.get("service_center_ids") or [])]


def resume_at(holiday: dict, pool: list[dict] | None = None, center_id: str | None = None) -> datetime:
    """When booking reopens after this holiday — skipping past any other
    live holiday that touches or overlaps it (and the ones touching that)."""
    resume = resume_point(holiday["end_at"])
    def same(h: dict) -> bool:
        return h is holiday or (h.get("_id") is not None and h.get("_id") == holiday.get("_id"))

    others = [h for h in _chain_pool(holiday, pool or [], center_id) if not same(h)]
    changed = True
    while changed:
        changed = False
        for h in others:
            nxt = resume_point(h["end_at"])
            if h["start_at"] <= resume < nxt:
                resume = nxt
                changed = True
    return resume


def _day(moment: datetime, *, year: bool = False) -> str:
    m = moment.astimezone(IST)
    return f"{m.day} {m.strftime('%b %Y' if year else '%b')}"


def _clock(moment: datetime) -> str:
    return format_time_12h(moment.astimezone(IST).strftime("%H:%M"))


def dates_label(holiday: dict) -> str:
    """"1 Oct – 2 Oct", "1 Oct", or with times when it starts/ends mid-day
    ("1 Oct, 2:00 PM – 6:00 PM", "1 Oct, from 2:00 PM", "1 Oct, 2:00 PM – 2 Oct")."""
    s = holiday["start_at"].astimezone(IST)
    e = holiday["end_at"].astimezone(IST)
    full_start = (s.hour, s.minute) == (0, 0)
    full_end = _is_day_end(e)
    same_day = s.date() == e.date()
    if full_start and full_end:
        return _day(s) if same_day else f"{_day(s)} – {_day(e)}"
    if same_day:
        if full_end:
            return f"{_day(s)}, from {_clock(s)}"
        if full_start:
            return f"{_day(s)}, until {_clock(e)}"
        return f"{_day(s)}, {_clock(s)} – {_clock(e)}"
    left = _day(s) if full_start else f"{_day(s)}, {_clock(s)}"
    right = _day(e) if full_end else f"{_day(e)}, {_clock(e)}"
    return f"{left} – {right}"


def resume_label(resume: datetime) -> str:
    """"3 Oct 2026", or "3 Oct 2026, 2:00 PM" when it reopens mid-day."""
    r = resume.astimezone(IST)
    if (r.hour, r.minute) == (0, 0):
        return _day(r, year=True)
    return f"{_day(r, year=True)}, {_clock(r)}"


def refusal_message(holiday: dict, resume: datetime) -> str:
    return f"We're on a holiday break on {dates_label(holiday)}. You can book from {resume_label(resume)}."


def _iso(moment: datetime | None) -> str | None:
    return _utc(moment).isoformat().replace("+00:00", "Z") if moment else None


def _slot_window(date_str: str, slot_key: str) -> tuple[datetime, datetime] | None:
    """A slot key "HH:MM-HH:MM" on an IST day → its two instants."""
    try:
        day = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        start_s, end_s = str(slot_key).split("-", 1)
        sh, sm = [int(p) for p in start_s.split(":")]
        eh, em = [int(p) for p in end_s.split(":")]
    except (TypeError, ValueError):
        return None
    base = day.replace(tzinfo=IST)
    return base + timedelta(hours=sh, minutes=sm), base + timedelta(hours=eh, minutes=em)


def _parse_day(value: str, what: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise BadRequestException(f"Pick a valid {what} date.")


def _parse_clock(value: str | None) -> time | None:
    if not value:
        return None
    hh, mm = [int(p) for p in value.split(":")]
    return time(hh, mm)


class HolidayService:
    def __init__(self, db):
        self.db = db
        self.repo = HolidayRepository(db)

    # ------------------------------------------------------------ live list

    async def live_holidays(self) -> list[dict]:
        """Every active holiday that hasn't ended — cached 30 s per process."""
        key = getattr(self.db, "name", "default")
        hit = _cache.get(key)
        now_mono = _time.monotonic()
        if hit and now_mono - hit[0] < CACHE_SECONDS:
            return hit[1]
        generation = _generation
        rows = await self.db.holidays.find(
            {"active": True, "end_at": {"$gt": datetime.now(timezone.utc)}, "is_deleted": {"$ne": True}}
        ).sort("start_at", 1).to_list(length=500)
        live = [_normalize(r) for r in rows]
        if generation == _generation:
            _cache[key] = (now_mono, live)
        return live

    async def center_holidays(self, center_id: str | None) -> list[dict]:
        return [h for h in await self.live_holidays() if applies_to(h, center_id)]

    async def blocking_holiday(self, center_id: str, slot_start: datetime, slot_end: datetime) -> dict | None:
        """The holiday overlapping that window at that center, if any."""
        start, end = _utc(slot_start), _utc(slot_end)
        for h in await self.live_holidays():
            if applies_to(h, center_id) and overlaps(h, start, end):
                return h
        return None

    async def holiday_for_slot(self, center_id: str, date_str: str, slot_key: str) -> dict | None:
        window = _slot_window(date_str, slot_key)
        if window is None:
            return None
        return await self.blocking_holiday(center_id, *window)

    async def refusal(self, holiday: dict, center_id: str | None) -> HolidayException:
        pool = await self.live_holidays()
        resume = resume_at(holiday, pool, center_id)
        return HolidayException(refusal_message(holiday, resume), details={
            "holiday_id": str(holiday.get("_id") or ""),
            "dates_label": dates_label(holiday),
            "resume_at": _iso(resume),
            "resume_label": resume_label(resume),
            "resume_date": resume.astimezone(IST).date().isoformat(),
        })

    async def ensure_bookable(
        self, center_id: str, date_str: str | None = None, slot_key: str | None = None,
        *, slot_start: datetime | None = None, slot_end: datetime | None = None,
    ) -> None:
        """THE check: raise HolidayException (400, error_code HOLIDAY) when
        that slot at that center falls inside a holiday."""
        if slot_start is not None and slot_end is not None:
            holiday = await self.blocking_holiday(center_id, slot_start, slot_end)
        else:
            holiday = await self.holiday_for_slot(center_id, date_str or "", slot_key or "")
        if holiday is not None:
            raise await self.refusal(holiday, center_id)

    def slot_marker(self, holiday: dict, pool: list[dict], center_id: str) -> dict:
        """The extra fields a holiday slot carries in available_slots."""
        resume = resume_at(holiday, pool, center_id)
        return {
            "holiday": True,
            "holiday_message": holiday.get("message") or "",
            "resume_label": resume_label(resume),
        }

    # ------------------------------------------------------------ public

    def public_item(self, holiday: dict, pool: list[dict], center_id: str | None = None, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        resume = resume_at(holiday, pool, center_id)
        r_ist = resume.astimezone(IST)
        return {
            "id": str(holiday.get("_id") or ""),
            "scope": holiday.get("scope") or "all",
            "message": holiday.get("message") or "",
            "title": holiday.get("title") or None,
            "starts_at": _iso(holiday["start_at"]),
            "ends_at": _iso(holiday["end_at"]),
            "is_active_now": holiday["start_at"] <= now < holiday["end_at"],
            "dates_label": dates_label(holiday),
            "resume_at": _iso(resume),
            "resume_label": resume_label(resume),
            "resume_date": r_ist.date().isoformat(),
            "resume_day": _day(r_ist),
        }

    async def public_list(self, center_id: str | None = None) -> list[dict]:
        """Holidays whose popup window is open now (notice_from ≤ now <
        end_at): without a center only the all-centers ones, with one also
        that center's."""
        center_id = center_id if center_id and ObjectId.is_valid(center_id) else None
        now = datetime.now(timezone.utc)
        pool = await self.live_holidays()
        out = []
        for h in pool:
            if not (h.get("notice_from") or h["start_at"]) <= now < h["end_at"]:
                continue
            if h.get("scope") != "all" and not (center_id and applies_to(h, center_id)):
                continue
            out.append(self.public_item(h, pool, center_id, now))
        return out

    async def customer_center_id(self, customer_id: str) -> str | None:
        """The center of the customer's default saved address, else of
        their most recent booking."""
        from app.core.exceptions import AppException
        from app.services.booking_service import BookingService

        address = await self.db.addresses.find_one(
            {"owner_id": customer_id, "is_deleted": {"$ne": True}}, sort=[("is_default", -1), ("updated_at", -1)]
        )
        if address:
            try:
                center, _ = await BookingService(self.db)._resolve_service_center(address, allow_pinless=True)
                return str(center["_id"])
            except AppException:
                pass
            except Exception:  # noqa: BLE001 — a lookup hiccup just means "no center"
                logger.exception("Could not resolve the customer's center for holidays")
        last = await self.db.bookings.find_one(
            {"customer_id": customer_id, "is_deleted": {"$ne": True}, "service_center_id": {"$ne": None}},
            {"service_center_id": 1}, sort=[("created_at", -1)],
        )
        return str(last["service_center_id"]) if last and last.get("service_center_id") else None

    async def for_customer(self, customer_id: str) -> list[dict]:
        return await self.public_list(await self.customer_center_id(customer_id))

    # ------------------------------------------------------------ admin

    @staticmethod
    def status_of(holiday: dict, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if not holiday.get("active") or holiday["end_at"] <= now:
            return "past"
        return "active" if holiday["start_at"] <= now else "upcoming"

    async def _center_names(self, ids: set[str]) -> dict[str, str]:
        oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
        if not oids:
            return {}
        rows = await self.db.service_centers.find({"_id": {"$in": oids}}, {"name": 1}).to_list(length=len(oids))
        return {str(r["_id"]): r.get("name") or "" for r in rows}

    def admin_item(self, holiday: dict, pool: list[dict], names: dict[str, str], now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        h = holiday
        s_ist, e_ist = h["start_at"].astimezone(IST), h["end_at"].astimezone(IST)
        resume = resume_at(h, pool) if h.get("active") else resume_point(h["end_at"])
        ended = None
        if not h.get("active"):
            ended = "ended_early" if h.get("ended_early") else "cancelled"
        return {
            "id": str(h.get("_id") or ""),
            "title": h.get("title") or None,
            "message": h.get("message") or "",
            "scope": h.get("scope") or "all",
            "service_center_ids": h.get("service_center_ids") or [],
            "centers": [{"id": c, "name": names.get(c) or "Unknown center"} for c in h.get("service_center_ids") or []],
            "start_at": _iso(h["start_at"]),
            "end_at": _iso(h["end_at"]),
            "notice_from": _iso(h.get("notice_from")),
            "start_date": h.get("start_date") or s_ist.date().isoformat(),
            "start_time": h.get("start_time"),
            "end_date": h.get("end_date") or e_ist.date().isoformat(),
            "end_time": h.get("end_time"),
            "notice_days_before": h.get("notice_days_before", DEFAULT_NOTICE_DAYS),
            "active": bool(h.get("active")),
            "status": self.status_of(h, now),
            "ended": ended,
            "original_end_at": _iso(h.get("original_end_at")),
            "dates_label": dates_label(h),
            "resume_at": _iso(resume),
            "resume_label": resume_label(resume),
            "resume_date": resume.astimezone(IST).date().isoformat(),
            "popup": self.public_item(h, pool, None, now),
            "created_by": h.get("created_by"),
            "created_at": _iso(h.get("created_at")),
            "updated_at": _iso(h.get("updated_at")),
            "cancelled_at": _iso(h.get("cancelled_at")),
            "cancelled_by": h.get("cancelled_by"),
        }

    async def list(self, status: str = "upcoming", page: int = 1, page_size: int = 20) -> tuple[list[dict], int]:
        now = datetime.now(timezone.utc)
        base: dict = {"is_deleted": {"$ne": True}}
        sort: list = [("start_at", -1)]
        if status == "upcoming":
            query = {**base, "active": True, "start_at": {"$gt": now}}
            sort = [("start_at", 1)]
        elif status == "active":
            query = {**base, "active": True, "start_at": {"$lte": now}, "end_at": {"$gt": now}}
            sort = [("start_at", 1)]
        elif status == "past":
            query = {**base, "$or": [{"active": {"$ne": True}}, {"end_at": {"$lte": now}}]}
            sort = [("end_at", -1)]
        elif status == "all":
            query = base
        else:
            raise BadRequestException("Pick upcoming, active, past or all.")
        total = await self.db.holidays.count_documents(query)
        rows = await self.db.holidays.find(query).sort(sort).skip(max(page - 1, 0) * page_size).limit(page_size).to_list(length=page_size)
        rows = [_normalize(r) for r in rows]
        pool = await self.live_holidays()
        names = await self._center_names({c for r in rows for c in r["service_center_ids"]})
        return [self.admin_item(r, pool, names, now) for r in rows], total

    async def _get(self, holiday_id: str) -> dict:
        doc = await self.repo.find_by_id(holiday_id) if ObjectId.is_valid(holiday_id or "") else None
        if not doc:
            raise NotFoundException("Holiday not found")
        return _normalize(doc)

    async def _validated(self, fields: dict, *, creating: bool, check_centers: bool = True) -> dict:
        """The stored fields for one holiday from the admin's IST input —
        every rule in one place (create, update and preview share it)."""
        message = (fields.get("message") or "").strip()
        if not message:
            raise BadRequestException("Write a short message for customers.")
        if len(message) > 300:
            raise BadRequestException("Keep the message to 300 characters.")
        scope = fields.get("scope") or "all"
        if scope not in ("all", "centers"):
            raise BadRequestException("Pick All Centers or Select Centers.")
        ids: list[str] = []
        if scope == "centers":
            ids = [str(x) for x in fields.get("service_center_ids") or []]
            if not ids:
                raise BadRequestException("Pick at least one service center.")
            if check_centers:
                if any(not ObjectId.is_valid(i) for i in ids):
                    raise BadRequestException("One of those service centers doesn't exist.")
                found = await self.db.service_centers.count_documents(
                    {"_id": {"$in": [ObjectId(i) for i in ids]}, "is_deleted": {"$ne": True}, **ACTIVE_CENTER}
                )
                if found != len(ids):
                    raise BadRequestException("Pick active service centers only — one of them is missing or switched off.")
        start_day = _parse_day(fields.get("start_date"), "start")
        end_day = _parse_day(fields.get("end_date"), "end")
        start_clock = _parse_clock(fields.get("start_time"))
        end_clock = _parse_clock(fields.get("end_time"))
        start_at = datetime.combine(start_day, start_clock or time(0, 0), IST).astimezone(timezone.utc)
        end_at = datetime.combine(end_day, end_clock or DAY_END, IST).astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if end_at <= start_at:
            raise BadRequestException("The holiday must end after it starts.")
        if end_at - start_at > timedelta(days=MAX_LENGTH_DAYS):
            raise BadRequestException(f"A holiday can be at most {MAX_LENGTH_DAYS} days long.")
        if start_at > now + timedelta(days=MAX_DAYS_AHEAD):
            raise BadRequestException("A holiday can start at most a year from today.")
        if creating and end_at <= now:
            raise BadRequestException("That time has already passed — pick dates from today onwards.")
        if not creating and end_at <= now:
            raise BadRequestException("That end time has already passed — use End Holiday Now instead.")
        notice_days = fields.get("notice_days_before")
        notice_days = DEFAULT_NOTICE_DAYS if notice_days is None else int(notice_days)
        if not 0 <= notice_days <= 30:
            raise BadRequestException("Show the popup 0 to 30 days before the holiday.")
        title = (fields.get("title") or "").strip() or None
        return {
            "title": title,
            "message": message,
            "scope": scope,
            "service_center_ids": ids,
            "start_at": start_at,
            "end_at": end_at,
            "notice_from": start_at - timedelta(days=notice_days),
            "notice_days_before": notice_days,
            "start_date": start_day.isoformat(),
            "start_time": fields.get("start_time") or None,
            "end_date": end_day.isoformat(),
            "end_time": fields.get("end_time") or None,
        }

    async def affected_bookings(self, scope: str, center_ids: list[str], start_at: datetime, end_at: datetime) -> dict:
        """Live bookings (one row per car) whose slot falls inside the
        holiday — bounded: a count (capped) and the first few."""
        query: dict = {
            "status": {"$in": list(ACTIVE_BOOKING_STATUSES)},
            "is_deleted": {"$ne": True},
            "slot_start": {"$lt": end_at},
            "slot_end": {"$gt": start_at},
        }
        if scope == "centers":
            query["service_center_id"] = {"$in": list(center_ids)}
        count = await self.db.bookings.count_documents(query, limit=AFFECTED_COUNT_CAP)
        rows = await self.db.bookings.find(
            query,
            {"booking_number": 1, "scheduled_slot": 1, "slot_start": 1, "service_center_id": 1, "customer_id": 1, "status": 1},
        ).sort("slot_start", 1).limit(AFFECTED_SAMPLE_SIZE).to_list(length=AFFECTED_SAMPLE_SIZE)
        names = await self._center_names({str(r.get("service_center_id")) for r in rows if r.get("service_center_id")})
        cust_ids = [ObjectId(r["customer_id"]) for r in rows if ObjectId.is_valid(str(r.get("customer_id") or ""))]
        customers = {
            str(u["_id"]): u.get("full_name") or u.get("phone") or ""
            for u in await self.db.users.find({"_id": {"$in": cust_ids}}, {"full_name": 1, "phone": 1}).to_list(length=len(cust_ids) or 1)
        } if cust_ids else {}
        sample = []
        for r in rows:
            start = _utc(r.get("slot_start"))
            sample.append({
                "booking_id": str(r["_id"]),
                "booking_number": r.get("booking_number"),
                "date": _day(start, year=True) if start else None,
                "slot": format_slot_12h(r.get("scheduled_slot")),
                "center_name": names.get(str(r.get("service_center_id"))) or "",
                "customer_name": customers.get(str(r.get("customer_id"))) or "",
                "status": r.get("status"),
            })
        return {"count": count, "sample": sample, "capped": count >= AFFECTED_COUNT_CAP}

    async def _response(self, doc: dict) -> dict:
        pool = await self.live_holidays()
        names = await self._center_names(set(doc["service_center_ids"]))
        return {
            "holiday": self.admin_item(doc, pool, names),
            "affected_bookings": await self.affected_bookings(doc["scope"], doc["service_center_ids"], doc["start_at"], doc["end_at"]),
        }

    async def preview(self, payload, *, holiday_id: str | None = None) -> dict:
        """Validation + labels + affected bookings, nothing written. With
        holiday_id it previews an edit of that holiday."""
        fields = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else dict(payload)
        existing = await self._get(holiday_id) if holiday_id else None
        merged = {**(self._input_of(existing) if existing else {}), **fields}
        doc = await self._validated(merged, creating=existing is None)
        doc.update({"active": True, "_id": existing["_id"] if existing else None})
        pool = [h for h in await self.live_holidays() if not existing or h.get("_id") != existing["_id"]]
        names = await self._center_names(set(doc["service_center_ids"]))
        item = self.admin_item(doc, pool, names)
        item["popup"] = self.public_item(doc, pool)
        return {"holiday": item, "affected_bookings": await self.affected_bookings(doc["scope"], doc["service_center_ids"], doc["start_at"], doc["end_at"])}

    @staticmethod
    def _input_of(doc: dict) -> dict:
        s_ist, e_ist = doc["start_at"].astimezone(IST), doc["end_at"].astimezone(IST)
        return {
            "title": doc.get("title"),
            "message": doc.get("message"),
            "scope": doc.get("scope"),
            "service_center_ids": doc.get("service_center_ids"),
            "start_date": doc.get("start_date") or s_ist.date().isoformat(),
            "start_time": doc.get("start_time"),
            "end_date": doc.get("end_date") or e_ist.date().isoformat(),
            "end_time": doc.get("end_time"),
            "notice_days_before": doc.get("notice_days_before", DEFAULT_NOTICE_DAYS),
        }

    async def create(self, payload, actor_id: str, actor_role: str = "admin") -> dict:
        from app.services.audit_service import AuditService

        doc = await self._validated(payload.model_dump(), creating=True)
        now = datetime.now(timezone.utc)
        doc.update({
            "active": True, "created_by": actor_id, "created_at": now, "updated_at": now,
            "cancelled_at": None, "cancelled_by": None,
        })
        created = await self.repo.create(doc)
        invalidate_holiday_cache()
        result = await self._response(_normalize(created))
        await AuditService(self.db).log_action(actor_id, actor_role, "HOLIDAY_CREATE", "holidays", str(created["_id"]), {
            "scope": doc["scope"], "service_center_ids": doc["service_center_ids"],
            "dates": result["holiday"]["dates_label"], "message": doc["message"],
            "affected_bookings": result["affected_bookings"]["count"],
        })
        return result

    async def update(self, holiday_id: str, payload, actor_id: str, actor_role: str = "admin") -> dict:
        from app.services.audit_service import AuditService, field_changes

        existing = await self._get(holiday_id)
        if self.status_of(existing) == "past":
            raise BadRequestException("This holiday has already ended — create a new one instead.")
        sent = payload.model_dump(exclude_unset=True)
        if not sent:
            raise BadRequestException("Nothing to update")
        merged = {**self._input_of(existing), **sent}
        check_centers = "scope" in sent or "service_center_ids" in sent
        doc = await self._validated(merged, creating=False, check_centers=check_centers)
        if self.status_of(existing) == "active" and doc["start_at"] != existing["start_at"] and doc["start_at"] > datetime.now(timezone.utc):
            # A running holiday can't be pushed back into the future — that
            # would quietly reopen today's slots; end it and make a new one.
            raise BadRequestException("This holiday has already started — its start can't move later. End it and create a new one.")
        updated = await self.db.holidays.find_one_and_update(
            {"_id": existing["_id"], "active": True},
            {"$set": {**doc, "updated_at": datetime.now(timezone.utc), "updated_by": actor_id}},
            return_document=True,
        )
        if not updated:
            raise BadRequestException("This holiday was just ended — refresh and try again.")
        invalidate_holiday_cache()
        updated = _normalize(updated)
        result = await self._response(updated)
        keys = [k for k in doc if k not in ("notice_from",)]
        await AuditService(self.db).log_action(actor_id, actor_role, "HOLIDAY_UPDATE", "holidays", holiday_id, {
            "changes": field_changes(existing, updated, keys),
            "affected_bookings": result["affected_bookings"]["count"],
        })
        return result

    async def end(self, holiday_id: str, actor_id: str, actor_role: str = "admin") -> dict:
        """End Now: a running holiday stops this moment (booking reopens at
        once); an upcoming one is cancelled."""
        from app.services.audit_service import AuditService

        existing = await self._get(holiday_id)
        status = self.status_of(existing)
        if status == "past":
            raise BadRequestException("This holiday has already ended.")
        now = datetime.now(timezone.utc)
        update: dict = {"active": False, "cancelled_at": now, "cancelled_by": actor_id, "updated_at": now}
        if status == "active":
            update.update({"end_at": now, "original_end_at": existing["end_at"], "ended_early": True})
        ended = await self.db.holidays.find_one_and_update(
            {"_id": existing["_id"], "active": True}, {"$set": update}, return_document=True,
        )
        if not ended:
            raise BadRequestException("This holiday has already ended.")
        invalidate_holiday_cache()
        ended = _normalize(ended)
        pool = await self.live_holidays()
        names = await self._center_names(set(ended["service_center_ids"]))
        await AuditService(self.db).log_action(actor_id, actor_role, "HOLIDAY_END", "holidays", holiday_id, {
            "was": status, "dates": dates_label(existing),
        })
        return {"holiday": self.admin_item(ended, pool, names)}
