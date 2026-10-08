"""
Society premium-wash scheduling — see docs/SOCIETY_PLANS.md §9.

Managers (own center) and admin (all) decide WHEN captains go to a society
for the residents' premium washes (Star Wash / Deep Cleaning) and HOW MANY
washes a captain does that day:

  * rules        — a society's repeat visit day ("every 2 weeks on Saturday,
                   8–2, two captains, 6 washes each"; a rotation across a
                   center's societies is just one such rule per society) or
                   one resident's own repeat wash ("every Saturday, 8–11");
  * visits       — the occurrences, materialized a plan month ahead
                   (idempotent: one per rule per date), each changeable on
                   its own: skip, move, other captains, take a car off;
  * allocation   — which due cars each visit day covers (engine.project):
                   most urgent first, spaced over the plan month, never more
                   than a car's remaining premium washes, capacity-bound;
  * generation   — N days ahead the reminder-loop sweep turns a visit into
                   real bookings through SocietyService.book_premium (the
                   same path the manager's "Book premium" uses, so quotas
                   are spent and refunded exactly as today), then assigns
                   the captains at staggered start times. Re-running it
                   never double-books (bookings for a car on that date are
                   adopted, not recreated).
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, time, timedelta

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.authz import ensure_own_center
from app.core.exceptions import AppException, BadRequestException, ForbiddenException, NotFoundException
from app.repositories.society_schedule_repository import (
    ScheduleRequestRepository,
    ScheduleRuleRepository,
    SocietyVisitRepository,
)
from app.services.society_schedule_engine import (
    Car,
    Slot,
    Visit,
    min_to_hhmm,
    pattern_dates,
    pattern_label,
    project,
    rotation_patterns,
    slot_from_key,
)
from app.services.society_service import LIVE_BOOKING_STATUSES, SocietyService, today_ist
from app.utils.quiet_alerts import muted_manager_new_booking_alerts
from app.utils.slots import format_slot_12h, format_time_12h
from app.utils.timezone import IST, from_stored, now_ist, to_ist

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system:society-schedule"
# Rules are materialized this far ahead: a full plan month and a bit.
HORIZON_DAYS = 35
# Nobody plans (or materializes) further out than this.
MAX_AHEAD_DAYS = 92
MAX_RANGE_DAYS = 63
# A sweep that died mid-generation hands the visit back after this.
STALE_CLAIM = timedelta(minutes=10)
# Residents' booking confirmations go out in the day (IST). A visit due
# tomorrow is generated whatever the hour — it's the last chance.
GENERATE_HOURS = (8, 21)
DEFAULT_SETTINGS = {"generate_days_ahead": 2, "default_washes_per_captain": 6}
GENERATED = ("booked", "partial", "failed", "empty")
OPEN = ("planned",) + GENERATED
CAPTAIN_BUSY_STATUSES = ["assigned", "captain_on_the_way", "service_started"]
CANCELLABLE = {"awaiting_payment", "pending", "assigned", "rescheduled"}
MAX_VISITS = 1500
MAX_SUBS = 3000
MAX_BOOKINGS = 6000


def _d(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def date_label(day: str | date) -> str:
    d = _d(day)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def window_label(slot_keys: list[str]) -> str:
    slots = sorted((slot_from_key(k) for k in slot_keys or []), key=lambda s: s.start)
    if not slots:
        return ""
    return format_slot_12h(f"{min_to_hhmm(slots[0].start)}-{min_to_hhmm(max(s.end for s in slots))}")


def _booking_day(b: dict) -> date | None:
    sd = b.get("scheduled_date")
    if isinstance(sd, datetime):
        return to_ist(sd).date()
    if isinstance(sd, str) and len(sd) >= 10:
        return date.fromisoformat(sd[:10])
    return None


class SocietyScheduleService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.rules = ScheduleRuleRepository(db)
        self.visits = SocietyVisitRepository(db)
        self.requests = ScheduleRequestRepository(db)
        self.society = SocietyService(db)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def settings(self) -> dict:
        doc = await self.db.society_settings.find_one({"_id": "schedule"}) or {}
        return {k: int(doc.get(k) or v) for k, v in DEFAULT_SETTINGS.items()}

    async def save_settings(self, payload, actor_id: str) -> dict:
        await self.db.society_settings.update_one(
            {"_id": "schedule"}, {"$set": {**payload.model_dump(), "updated_by": actor_id, "updated_at": now_ist()}}, upsert=True
        )
        return await self.settings()

    # ------------------------------------------------------------------
    # Lookups + access
    # ------------------------------------------------------------------

    async def _center(self, center_id: str | None) -> dict:
        center = await self.db.service_centers.find_one({"_id": ObjectId(center_id)}) if center_id and ObjectId.is_valid(center_id) else None
        if not center:
            raise NotFoundException("Service center not found")
        return center

    async def _slots(self, center: dict) -> dict[str, Slot]:
        """The center's slots — the booking engine's own list
        (booking_service.center_slots: the center's hours, else the
        platform's 7:00 AM – 7:00 PM, and its slot length), never a
        hard-coded 08:00–20:00 that offers slots the engine refuses."""
        from app.services.booking_policy_service import BookingPolicyService
        from app.services.booking_service import center_slots

        policy = await BookingPolicyService(self.db).get_policy()
        return {s["key"]: slot_from_key(s["key"]) for s in center_slots(center, policy)}

    async def _policy(self) -> dict:
        from app.services.booking_policy_service import BookingPolicyService

        return await BookingPolicyService(self.db).get_policy()

    async def _people(self, ids) -> dict[str, str]:
        users = await self.society._users_by_id([i for i in ids if i])
        return {k: u.get("full_name") or "Captain" for k, u in users.items()}

    async def _check_captains(self, center_id: str, ids: list[str]) -> None:
        ids = [i for i in ids or [] if i]
        if not ids:
            return
        oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
        count = await self.db.users.count_documents({
            "_id": {"$in": oids}, "role": "captain", "service_center_id": center_id,
            "is_deleted": {"$ne": True}, "status": {"$nin": ["suspended", "inactive"]},
        }) if len(oids) == len(ids) else -1
        if count != len(set(ids)):
            raise BadRequestException("Pick captains from this society's service center.")

    @staticmethod
    def _check_slots(slots: dict[str, Slot], keys: list[str]) -> list[str]:
        bad = [k for k in keys if k not in slots]
        if bad:
            raise BadRequestException("Pick slots this service center offers.")
        return sorted(set(keys), key=lambda k: slots[k].start)

    @staticmethod
    def _check_date(day: str, *, max_days: int = MAX_AHEAD_DAYS) -> date:
        d = _d(day)
        today = today_ist()
        if d <= today:
            raise BadRequestException("Pick tomorrow or later — residents get a day's notice.")
        if d > today + timedelta(days=max_days):
            raise BadRequestException(f"Plan at most {max_days} days ahead.")
        return d

    async def society_for_staff(self, society_id: str, user) -> dict:
        return await self.society.society_for_actor(society_id, user.role, user.service_center_id)

    async def visit_for_staff(self, visit_id: str, user) -> dict:
        visit = await self.visits.find_by_id(visit_id) if ObjectId.is_valid(visit_id or "") else None
        if not visit:
            raise NotFoundException("Visit not found")
        if user.role not in ("admin", "manager"):
            raise ForbiddenException("You don't have access to this visit")
        ensure_own_center(user.role, user.service_center_id, visit.get("service_center_id"))
        return visit

    async def rule_for_staff(self, rule_id: str, user) -> dict:
        rule = await self.rules.find_by_id(rule_id) if ObjectId.is_valid(rule_id or "") else None
        if not rule:
            raise NotFoundException("Rule not found")
        if user.role not in ("admin", "manager"):
            raise ForbiddenException("You don't have access to this rule")
        ensure_own_center(user.role, user.service_center_id, rule.get("service_center_id"))
        return rule

    async def request_for_staff(self, request_id: str, user) -> dict:
        req = await self.requests.find_by_id(request_id) if ObjectId.is_valid(request_id or "") else None
        if not req:
            raise NotFoundException("Request not found")
        if user.role not in ("admin", "manager"):
            raise ForbiddenException("You don't have access to this request")
        ensure_own_center(user.role, user.service_center_id, req.get("service_center_id"))
        return req

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _rule_dates(self, payload) -> tuple[str, str | None]:
        tomorrow = today_ist() + timedelta(days=1)
        start = max(_d(payload.start_date), tomorrow) if payload.start_date else tomorrow
        if payload.end_date and _d(payload.end_date) < start:
            raise BadRequestException("The end date must be tomorrow or later.")
        return start.isoformat(), payload.end_date

    async def _society_rule_fields(self, society: dict, payload) -> dict:
        center = await self._center(society.get("service_center_id"))
        slots = await self._slots(center)
        keys = self._check_slots(slots, payload.slot_keys)
        await self._check_captains(society["service_center_id"], payload.captain_ids)
        start, end = self._rule_dates(payload)
        return {
            "pattern": payload.pattern.model_dump(), "slot_keys": keys, "captain_ids": list(payload.captain_ids),
            "washes_per_captain": int(payload.washes_per_captain), "start_date": start, "end_date": end,
            "notes": payload.notes, "is_active": bool(payload.is_active),
        }

    async def _resident_rule_fields(self, society: dict, payload, rule_id: str | None = None) -> dict:
        sid = str(society["_id"])
        enrollment = await self.society.get_enrollment(payload.enrollment_id)
        if enrollment.get("society_id") != sid or enrollment.get("status") != "active":
            raise BadRequestException("Pick an active resident of this society.")
        subs = await self.society._subs_by_id(payload.subscription_ids)
        now = now_ist()
        from app.services.society_service import sub_is_live

        if len(subs) != len(set(payload.subscription_ids)) or any(
            s.get("enrollment_id") != str(enrollment["_id"]) or not sub_is_live(s, now) for s in subs.values()
        ):
            raise BadRequestException("Pick this resident's cars that are on a live plan.")
        clash = await self.db.society_schedule_rules.find_one({
            "kind": "resident", "enrollment_id": str(enrollment["_id"]), "is_active": True, "is_deleted": {"$ne": True},
            "subscription_ids": {"$in": list(payload.subscription_ids)},
            **({"_id": {"$ne": ObjectId(rule_id)}} if rule_id else {}),
        })
        if clash:
            raise BadRequestException("One of these cars already has a repeat wash — edit that one instead.")
        center = await self._center(society.get("service_center_id"))
        slots = await self._slots(center)
        self._check_slots(slots, [payload.slot_key])
        if payload.captain_id:
            await self._check_captains(society["service_center_id"], [payload.captain_id])
        start, end = self._rule_dates(payload)
        return {
            "pattern": payload.pattern.model_dump(), "slot_keys": [payload.slot_key],
            "captain_ids": [payload.captain_id] if payload.captain_id else [], "washes_per_captain": 99,
            "enrollment_id": str(enrollment["_id"]), "customer_id": enrollment.get("customer_id"),
            "subscription_ids": list(dict.fromkeys(payload.subscription_ids)),
            "start_date": start, "end_date": end, "notes": payload.notes, "is_active": bool(payload.is_active),
        }

    async def create_rule(self, society: dict, payload, kind: str, actor_id: str) -> dict:
        fields = await (self._society_rule_fields(society, payload) if kind == "society" else self._resident_rule_fields(society, payload))
        rule = await self.rules.create({
            "society_id": str(society["_id"]), "service_center_id": society.get("service_center_id"), "kind": kind,
            **fields, "materialized_until": None, "created_by": actor_id, "updated_by": actor_id,
        })
        await self.materialize(rule)
        return (await self.rule_views([rule]))[0]

    async def update_rule(self, rule: dict, payload, actor_id: str) -> dict:
        society = await self.society.get_society(rule["society_id"])
        if rule["kind"] == "society":
            fields = await self._society_rule_fields(society, payload)
        else:
            fields = await self._resident_rule_fields(society, payload, rule_id=str(rule["_id"]))
        await self._clear_future(str(rule["_id"]))
        updated = await self.rules.update_by_id(str(rule["_id"]), {**fields, "materialized_until": None, "updated_by": actor_id})
        await self.materialize(updated)
        return (await self.rule_views([updated]))[0]

    async def delete_rule(self, rule: dict, actor_id: str) -> None:
        await self._clear_future(str(rule["_id"]))
        await self.rules.update_by_id(str(rule["_id"]), {"is_active": False, "is_deleted": True, "updated_by": actor_id})

    async def _clear_future(self, rule_id: str) -> int:
        """A rule changed: its not-yet-booked future days go (single-day
        changes to them included) and are re-made from the new rule."""
        tomorrow = (today_ist() + timedelta(days=1)).isoformat()
        result = await self.db.society_visits.delete_many(
            {"rule_id": rule_id, "date": {"$gte": tomorrow}, "status": {"$in": ["planned", "skipped"]}}
        )
        return result.deleted_count

    async def rule_views(self, rules: list[dict]) -> list[dict]:
        names = await self._people({c for r in rules for c in r.get("captain_ids") or []})
        out = []
        for r in rules:
            out.append({
                "id": str(r["_id"]),
                "society_id": r.get("society_id"),
                "kind": r.get("kind"),
                "pattern": r.get("pattern"),
                "label": pattern_label(r["pattern"]),
                "slot_keys": r.get("slot_keys") or [],
                "window_label": window_label(r.get("slot_keys") or []),
                "captains": [{"id": c, "name": names.get(c)} for c in r.get("captain_ids") or []],
                "washes_per_captain": r.get("washes_per_captain"),
                "enrollment_id": r.get("enrollment_id"),
                "subscription_ids": r.get("subscription_ids") or [],
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
                "notes": r.get("notes"),
                "is_active": bool(r.get("is_active")),
            })
        return out

    # ------------------------------------------------------------------
    # Materializing occurrences (bounded, idempotent)
    # ------------------------------------------------------------------

    async def materialize(self, rule: dict, until: date | None = None) -> int:
        """Visits for this rule from tomorrow (or where it got to) through
        `until` (default: a plan month ahead). One per date, keyed
        rule:date — running it again, from anywhere, adds nothing."""
        if not rule.get("is_active") or rule.get("is_deleted"):
            return 0
        today = today_ist()
        until = min(until or today + timedelta(days=HORIZON_DAYS), today + timedelta(days=MAX_AHEAD_DAYS))
        done = _d(rule["materialized_until"]) if rule.get("materialized_until") else None
        start = today + timedelta(days=1)
        if done and done >= start:
            start = done + timedelta(days=1)
        if start > until:
            return 0
        rule_start = _d(rule["start_date"]) if rule.get("start_date") else None
        rule_end = _d(rule["end_date"]) if rule.get("end_date") else None
        created = 0
        rid = str(rule["_id"])
        for day in pattern_dates(rule["pattern"], start, until, rule_start=rule_start, rule_end=rule_end):
            key = f"{rid}:{day.isoformat()}"
            doc = self._visit_doc(rule, day)
            try:
                result = await self.db.society_visits.update_one({"occurrence_key": key}, {"$setOnInsert": doc}, upsert=True)
                created += 1 if result.upserted_id else 0
            except DuplicateKeyError:
                continue
        await self.db.society_schedule_rules.update_one({"_id": rule["_id"]}, {"$max": {"materialized_until": until.isoformat()}})
        return created

    @staticmethod
    def _visit_doc(rule: dict, day: date) -> dict:
        now = now_ist()
        return {
            "society_id": rule["society_id"],
            "service_center_id": rule.get("service_center_id"),
            "kind": rule["kind"],
            "rule_id": str(rule["_id"]),
            "rule_date": day.isoformat(),
            "date": day.isoformat(),
            "slot_keys": list(rule.get("slot_keys") or []),
            "captain_ids": list(rule.get("captain_ids") or []),
            "washes_per_captain": int(rule.get("washes_per_captain") or 6),
            "enrollment_id": rule.get("enrollment_id"),
            "customer_id": rule.get("customer_id"),
            "subscription_ids": list(rule.get("subscription_ids") or []),
            "excluded_subscription_ids": [],
            "status": "planned",
            "allocations": [],
            "overflow": [],
            "changes": [],
            "notes": None,
            "created_by": "rule",
            "created_at": now,
            "updated_at": now,
            "is_deleted": False,
        }

    async def ensure_materialized(self, society_ids: list[str], until: date) -> None:
        if not society_ids:
            return
        until_s = min(until, today_ist() + timedelta(days=MAX_AHEAD_DAYS)).isoformat()
        rules = await self.db.society_schedule_rules.find({
            "society_id": {"$in": society_ids}, "is_active": True, "is_deleted": {"$ne": True},
            "$or": [{"materialized_until": None}, {"materialized_until": {"$lt": until_s}}],
        }).to_list(length=500)
        for rule in rules:
            await self.materialize(rule, _d(until_s))

    # ------------------------------------------------------------------
    # Loading + projecting
    # ------------------------------------------------------------------

    async def _load(self, societies: list[dict], through: date, *, since: date | None = None, see_through: set[str] | None = None) -> dict:
        """Everything a projection needs for these societies, in a handful of
        bounded, indexed reads. `see_through`: booking ids to treat as not
        made yet (their wash credited back, their captain time free) — a
        visit's own orphaned bookings when its generation is re-run, so the
        projection re-derives those units and they get adopted, not lost."""
        see_through = see_through or set()
        today = today_ist()
        since = min(since or today, today)
        sids = [str(s["_id"]) for s in societies]
        now = now_ist()
        subs = await self.db.user_subscriptions.find(
            {"society_id": {"$in": sids}, "status": "active", "end_date": {"$gt": now}, "is_deleted": {"$ne": True}}
        ).to_list(length=MAX_SUBS) if sids else []
        enrollments = await self.society._enrollments_by_id([s.get("enrollment_id") for s in subs])
        services = await self.society._services_by_id([s.get("service_id") for s in subs])
        type_names = await self.society._type_names()
        sub_ids = [str(s["_id"]) for s in subs]
        bookings = await self.db.bookings.find(
            {
                "subscription_id": {"$in": sub_ids}, "status": {"$in": LIVE_BOOKING_STATUSES + ["completed"]},
                "is_deleted": {"$ne": True}, "scheduled_date": {"$gte": datetime.combine(today - timedelta(days=40), time())},
            },
            {"subscription_id": 1, "scheduled_date": 1, "status": 1},
        ).to_list(length=MAX_BOOKINGS) if sub_ids else []
        washed: dict[str, set[date]] = {}
        credit: dict[str, int] = {}
        for b in bookings:
            if str(b["_id"]) in see_through:
                if b.get("status") in LIVE_BOOKING_STATUSES:
                    credit[b["subscription_id"]] = credit.get(b["subscription_id"], 0) + 1
                continue
            day = _booking_day(b)
            if day:
                washed.setdefault(b["subscription_id"], set()).add(day)
        visits = await self.db.society_visits.find(
            {"society_id": {"$in": sids}, "date": {"$gte": since.isoformat(), "$lte": through.isoformat()}, "is_deleted": {"$ne": True}}
        ).sort([("date", 1), ("created_at", 1)]).to_list(length=MAX_VISITS) if sids else []
        daily = {str(s["_id"]): s.get("daily_captain_id") for s in societies}
        captain_ids = {c for v in visits for c in v.get("captain_ids") or []} | {c for c in daily.values() if c}
        busy = await self._captain_busy(list(captain_ids), today, through, skip=see_through)
        cars, info = {}, {}
        for s in subs:
            sid = str(s["_id"])
            e = enrollments.get(s.get("enrollment_id") or "") or {}
            plate = next((c.get("registration_number") for c in e.get("cars") or [] if c.get("vehicle_id") == s.get("vehicle_id")), None)
            cycle = from_stored(s.get("cycle_start") or s["start_date"]).date()
            if cycle > today and s.get("prev_cycle_start"):
                cycle = from_stored(s["prev_cycle_start"]).date()
            service = services.get(s.get("service_id") or "") or {}
            cars[sid] = Car(
                sub_id=sid, vehicle_id=s.get("vehicle_id") or "", enrollment_id=s.get("enrollment_id") or "",
                customer_id=s.get("customer_id") or "", remaining=int(s.get("remaining_service_count") or 0) + credit.get(sid, 0),
                cycle_start=cycle, end=from_stored(s["end_date"]).date(), total=int(s.get("total_service_count") or 1),
                minutes=int(service.get("duration_minutes") or 45), flat=e.get("flat") or "", plate=plate or "",
                washed=washed.get(sid, set()),
            )
            info[sid] = {
                "sub_id": sid, "society_id": s.get("society_id"), "plate": plate, "flat": e.get("flat"),
                "resident_name": e.get("resident_name"), "enrollment_id": s.get("enrollment_id"),
                "customer_id": s.get("customer_id"), "vehicle_type_name": type_names.get(s.get("vehicle_type") or ""),
                "service_name": service.get("name"), "remaining": int(s.get("remaining_service_count") or 0),
                "total": int(s.get("total_service_count") or 0), "end_date": from_stored(s["end_date"]).date().isoformat(),
            }
        return {"subs": subs, "cars": cars, "info": info, "visits": visits, "daily": daily, "busy": busy, "washed": washed}

    async def _captain_busy(self, captain_ids: list[str], start: date, end: date, skip: set[str] | None = None) -> dict:
        """{date: {captain_id: [(start_min, end_min)…]}} from their assigned jobs."""
        from app.services.booking_service import _captain_booking_window

        if not captain_ids:
            return {}
        rows = await self.db.bookings.find(
            {
                "captain_id": {"$in": captain_ids}, "status": {"$in": CAPTAIN_BUSY_STATUSES}, "is_deleted": {"$ne": True},
                "scheduled_date": {"$gte": datetime.combine(start, time()), "$lt": datetime.combine(end + timedelta(days=1), time())},
            },
            {"captain_id": 1, "scheduled_date": 1, "scheduled_slot": 1, "estimated_start_at": 1, "duration_minutes": 1, "group_offset_minutes": 1},
        ).to_list(length=MAX_BOOKINGS)
        out: dict = {}
        for b in rows:
            if skip and str(b["_id"]) in skip:
                continue
            try:
                s, e = _captain_booking_window(b)
            except Exception:  # noqa: BLE001 — a malformed legacy row just isn't counted
                continue
            day = s.date()
            out.setdefault(day, {}).setdefault(b["captain_id"], []).append((s.hour * 60 + s.minute, e.hour * 60 + e.minute if e.date() == day else 24 * 60))
        return out

    def _project(self, ctx: dict, society_id: str, slots: dict[str, Slot], buffer: int, *, treat_planned: str | None = None,
                 busy: dict | None = None) -> dict[str, dict]:
        today = today_ist()
        daily = ctx["daily"].get(society_id)
        visits = []
        for i, v in enumerate(ctx["visits"]):
            if v["society_id"] != society_id or _d(v["date"]) < today:
                continue
            status = "planned" if str(v["_id"]) == treat_planned else v.get("status")
            captains = list(v.get("captain_ids") or [])
            if v.get("kind") == "resident" and not captains and daily:
                captains = [daily]
            visits.append(Visit(
                id=str(v["_id"]), kind=v.get("kind") or "society", date=_d(v["date"]), status=status,
                slot_keys=list(v.get("slot_keys") or []), captain_ids=captains, per_captain=int(v.get("washes_per_captain") or 6),
                sub_ids=list(v.get("subscription_ids") or []), excluded=set(v.get("excluded_subscription_ids") or []), order=i,
            ))
        cars = [c for sid, c in ctx["cars"].items() if ctx["info"][sid]["society_id"] == society_id]
        return project(cars, visits, slots, buffer, ctx["busy"] if busy is None else busy)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def _unit_view(self, unit: dict, info: dict, names: dict[str, str], status: str) -> dict:
        cars = [info.get(s) or {"sub_id": s} for s in unit.get("sub_ids") or []]
        first = cars[0] if cars else {}
        start = unit.get("start")
        return {
            "enrollment_id": unit.get("enrollment_id"),
            "resident_name": first.get("resident_name") or unit.get("resident_name"),
            "flat": first.get("flat") or unit.get("flat"),
            "cars": [
                {"sub_id": c.get("sub_id"), "plate": c.get("plate") or c.get("registration_number"), "vehicle_type_name": c.get("vehicle_type_name"),
                 "service_name": c.get("service_name")}
                for c in cars
            ],
            "captain_id": unit.get("captain_id"),
            "captain_name": names.get(unit.get("captain_id") or ""),
            "slot_key": unit.get("slot_key"),
            "slot_label": format_slot_12h(unit["slot_key"]) if unit.get("slot_key") else None,
            "start": min_to_hhmm(start) if isinstance(start, int) else unit.get("start_hhmm"),
            "start_label": format_time_12h(min_to_hhmm(start)) if isinstance(start, int) else (format_time_12h(unit["start_hhmm"]) if unit.get("start_hhmm") else None),
            "status": status,
            "booking_ids": unit.get("booking_ids") or [],
            "booking_statuses": unit.get("booking_statuses") or [],
            "error": unit.get("error"),
            "warning": unit.get("warning"),
            "reason": unit.get("reason"),
            "lane": unit.get("lane"),
        }

    async def _visit_views(self, visits: list[dict], ctx: dict, projections: dict[str, dict], societies: dict[str, dict], settings: dict) -> list[dict]:
        names = await self._people(
            {c for v in visits for c in v.get("captain_ids") or []}
            | {a.get("captain_id") for v in visits for a in v.get("allocations") or []}
            | {u.get("captain_id") for p in projections.values() for u in p.get("placed") or []}
            | {c for c in ctx["daily"].values() if c}
        )
        visit_ids = [str(v["_id"]) for v in visits if v.get("status") in GENERATED]
        live = {}
        if visit_ids:
            rows = await self.db.bookings.find(
                {"society_visit_id": {"$in": visit_ids}}, {"status": 1, "captain_id": 1, "booking_number": 1}
            ).to_list(length=MAX_BOOKINGS)
            live = {str(b["_id"]): b for b in rows}
        today = today_ist()
        lead = settings["generate_days_ahead"]
        out = []
        for v in visits:
            vid = str(v["_id"])
            day = _d(v["date"])
            status = v.get("status")
            society = societies.get(v["society_id"]) or {}
            allocations, overflow, skipped = [], [], []
            if status in GENERATED:
                for a in v.get("allocations") or []:
                    statuses = [(live.get(b) or {}).get("status") for b in a.get("booking_ids") or []]
                    a = {**a, "booking_statuses": statuses}
                    allocations.append(self._unit_view(a, ctx["info"], names, a.get("status") or "booked"))
                overflow = [self._unit_view(u, ctx["info"], names, "overflow") for u in v.get("overflow") or []]
            elif status == "planned" and day >= today:
                p = projections.get(vid) or {}
                allocations = [self._unit_view(u, ctx["info"], names, "projected") for u in p.get("placed") or []]
                overflow = [self._unit_view(u, ctx["info"], names, "overflow") for u in p.get("overflow") or []]
                skipped = [{**(ctx["info"].get(s["sub_id"]) or {"sub_id": s["sub_id"]}), "reason": s["reason"]} for s in p.get("skipped") or []]
            captains = list(v.get("captain_ids") or [])
            fallback = None
            if v.get("kind") == "resident" and not captains and ctx["daily"].get(v["society_id"]):
                fallback = ctx["daily"][v["society_id"]]
            display = status
            if status == "planned" and day < today:
                display = "missed"
            elif status in GENERATED and day < today:
                display = "done"
            lanes = max(1, len(captains))
            out.append({
                "id": vid,
                "society_id": v["society_id"],
                "society_name": society.get("name"),
                "kind": v.get("kind"),
                "date": v["date"],
                "date_label": date_label(day),
                "rule_id": v.get("rule_id"),
                "rule_date": v.get("rule_date"),
                "moved_from": v.get("moved_from"),
                "slot_keys": v.get("slot_keys") or [],
                "window_label": window_label(v.get("slot_keys") or []),
                "captains": [{"id": c, "name": names.get(c)} for c in captains],
                "fallback_captain": {"id": fallback, "name": names.get(fallback)} if fallback else None,
                "washes_per_captain": v.get("washes_per_captain"),
                "capacity": lanes * int(v.get("washes_per_captain") or 0) if v.get("kind") == "society" else None,
                "enrollment_id": v.get("enrollment_id"),
                "subscription_ids": v.get("subscription_ids") or [],
                "excluded_subscription_ids": v.get("excluded_subscription_ids") or [],
                "status": status,
                "display_status": display,
                "generate_on": (day - timedelta(days=lead)).isoformat() if status == "planned" and day >= today else None,
                "generated_at": from_stored(v["generated_at"]).isoformat() if v.get("generated_at") else None,
                "generation_error": v.get("generation_error"),
                "allocations": allocations,
                "allocated_cars": sum(len(a["cars"]) for a in allocations if a["status"] in ("projected", "booked")),
                "overflow": overflow,
                "skipped": skipped,
                "changes": (v.get("changes") or [])[-5:],
                "notes": v.get("notes"),
                "editable": day > today and status in OPEN + ("skipped",),
            })
        return out

    def _outlook(self, ctx: dict, projections: dict[str, dict], society_id: str | None = None) -> list[dict]:
        """Per car: premium washes left, how many the schedule covers before
        its plan month ends, and how many have no date yet (at risk)."""
        today = today_ist()
        planned: dict[str, int] = {}
        for p in projections.values():
            for u in p.get("placed") or []:
                for s in u.get("sub_ids") or []:
                    planned[s] = planned.get(s, 0) + 1
        out = []
        for sid, info in ctx["info"].items():
            if society_id and info["society_id"] != society_id:
                continue
            upcoming = sum(1 for d in ctx["washed"].get(sid, set()) if d >= today)
            covered = planned.get(sid, 0)
            out.append({
                **info,
                "booked_upcoming": upcoming,
                "planned": covered,
                "at_risk": max(0, info["remaining"] - covered),
            })
        out.sort(key=lambda r: (-r["at_risk"], (r.get("flat") or "").lower(), r.get("plate") or ""))
        return out

    # ------------------------------------------------------------------
    # Planner (center) + society schedule
    # ------------------------------------------------------------------

    def _range(self, start: str | None, end: str | None, default_days: int = 28) -> tuple[date, date]:
        today = today_ist()
        s = _d(start) if start else today
        e = _d(end) if end else s + timedelta(days=default_days - 1)
        if e < s:
            raise BadRequestException("The end date must be after the start date.")
        if (e - s).days > MAX_RANGE_DAYS:
            e = s + timedelta(days=MAX_RANGE_DAYS)
        e = min(e, today + timedelta(days=MAX_AHEAD_DAYS))
        s = max(s, today - timedelta(days=MAX_RANGE_DAYS))
        return s, e

    async def planner(self, center_id: str, start: str | None, end: str | None) -> dict:
        center = await self._center(center_id)
        s, e = self._range(start, end)
        today = today_ist()
        societies = await self.db.societies.find(
            {"service_center_id": center_id, "is_deleted": {"$ne": True}, "is_active": {"$ne": False}}
        ).sort("name", 1).to_list(length=500)
        sids = [str(x["_id"]) for x in societies]
        await self.ensure_materialized(sids, max(e, today + timedelta(days=HORIZON_DAYS)))
        through = max(e, today + timedelta(days=HORIZON_DAYS))
        ctx = await self._load(societies, through, since=s)
        slots = await self._slots(center)
        policy = await self._policy()
        buffer = int(policy.get("captain_travel_buffer_minutes", 15))
        shared_busy = {d: {k: list(v) for k, v in lanes.items()} for d, lanes in ctx["busy"].items()}
        projections: dict[str, dict] = {}
        for sid in sids:
            projections.update(self._project(ctx, sid, slots, buffer, busy=shared_busy))
        settings = await self.settings()
        by_id = {str(x["_id"]): x for x in societies}
        in_range = [v for v in ctx["visits"] if s <= _d(v["date"]) <= e]
        views = await self._visit_views(in_range, ctx, projections, by_id, settings)
        rules = await self.db.society_schedule_rules.find(
            {"society_id": {"$in": sids}, "kind": "society", "is_deleted": {"$ne": True}}
        ).to_list(length=500)
        rule_views = await self.rule_views(rules)
        outlook = self._outlook(ctx, projections)
        rows = []
        for x in societies:
            sid = str(x["_id"])
            nxt = next((v for v in ctx["visits"] if v["society_id"] == sid and v.get("kind") == "society"
                        and _d(v["date"]) > today and v.get("status") in OPEN), None)
            mine = [o for o in outlook if o["society_id"] == sid]
            rows.append({
                "id": sid, "name": x.get("name"), "area": x.get("area"),
                "active_cars": len(mine),
                "premium_left": sum(o["remaining"] for o in mine),
                "at_risk": sum(o["at_risk"] for o in mine),
                "rules": [r for r in rule_views if r["society_id"] == sid],
                "next_visit": {"id": str(nxt["_id"]), "date": nxt["date"], "date_label": date_label(nxt["date"])} if nxt else None,
            })
        warnings = await self._warnings(views, slots, center_id)
        captains = await self.society.captains_for_center(center_id)
        return {
            "center": {"id": center_id, "name": center.get("name")},
            "start": s.isoformat(), "end": e.isoformat(), "today": today.isoformat(),
            "settings": settings,
            "slots": [{"key": k, "label": format_slot_12h(k)} for k in sorted(slots, key=lambda k: slots[k].start)],
            "captains": captains,
            "societies": rows,
            "visits": views,
            "warnings": warnings,
        }

    async def society_schedule(self, society: dict, start: str | None, end: str | None) -> dict:
        sid = str(society["_id"])
        center = await self._center(society.get("service_center_id"))
        s, e = self._range(start, end, default_days=35)
        today = today_ist()
        through = max(e, today + timedelta(days=HORIZON_DAYS))
        await self.ensure_materialized([sid], through)
        ctx = await self._load([society], through, since=s)
        slots = await self._slots(center)
        policy = await self._policy()
        projections = self._project(ctx, sid, slots, int(policy.get("captain_travel_buffer_minutes", 15)))
        settings = await self.settings()
        in_range = [v for v in ctx["visits"] if s <= _d(v["date"]) <= e]
        views = await self._visit_views(in_range, ctx, projections, {sid: society}, settings)
        rules = await self.db.society_schedule_rules.find({"society_id": sid, "is_deleted": {"$ne": True}}).sort("created_at", 1).to_list(length=200)
        rule_views = await self.rule_views(rules)
        requests = await self.request_views(await self.db.society_schedule_requests.find(
            {"society_id": sid, "status": "pending"}
        ).sort("created_at", 1).to_list(length=100))
        residents = []
        for e_row in await self.db.society_enrollments.find(
            {"society_id": sid, "status": "active", "is_deleted": {"$ne": True}}, {"resident_name": 1, "flat": 1, "phone": 1, "cars": 1}
        ).sort("flat", 1).to_list(length=500):
            cars = [ctx["info"][c["subscription_id"]] for c in e_row.get("cars") or [] if c.get("subscription_id") in ctx["info"]]
            if cars:
                residents.append({"enrollment_id": str(e_row["_id"]), "resident_name": e_row.get("resident_name"), "flat": e_row.get("flat"), "cars": cars})
        return {
            "society": {"id": sid, "name": society.get("name"), "service_center_id": society.get("service_center_id"), "daily_captain_id": society.get("daily_captain_id")},
            "start": s.isoformat(), "end": e.isoformat(), "today": today.isoformat(),
            "settings": settings,
            "slots": [{"key": k, "label": format_slot_12h(k)} for k in sorted(slots, key=lambda k: slots[k].start)],
            "captains": await self.society.captains_for_center(society["service_center_id"]),
            "rules": [r for r in rule_views if r["kind"] == "society"],
            "resident_rules": [r for r in rule_views if r["kind"] == "resident"],
            "visits": views,
            "outlook": self._outlook(ctx, projections, sid),
            "requests": requests,
            "residents": residents,
            "warnings": await self._warnings(views, slots, society["service_center_id"]),
        }

    async def _warnings(self, views: list[dict], slots: dict[str, Slot], center_id: str) -> list[dict]:
        today = today_ist()
        out: list[dict] = []
        upcoming = [v for v in views if _d(v["date"]) >= today and v["status"] in OPEN]
        # One captain on two society visits the same day.
        seen: dict[tuple[str, str], list[dict]] = {}
        for v in upcoming:
            for c in v["captains"]:
                seen.setdefault((v["date"], c["id"]), []).append(v)
        for (day, cid), vs in seen.items():
            if len(vs) > 1:
                who = vs[0]["captains"][[c["id"] for c in vs[0]["captains"]].index(cid)]["name"] or "A captain"
                out.append({"kind": "captain_double", "date": day, "visit_ids": [v["id"] for v in vs],
                            "message": f"{who} is on {len(vs)} visits on {date_label(day)} ({', '.join(v['society_name'] or '—' for v in vs)})."})
        # Captains on approved leave.
        dates = sorted({v["date"] for v in upcoming})
        cids = sorted({c["id"] for v in upcoming for c in v["captains"]})
        if dates and cids:
            leaves = await self.db.leave_requests.find({
                "captain_id": {"$in": cids}, "status": "approved", "is_deleted": {"$ne": True},
                "start_date": {"$lte": dates[-1]}, "end_date": {"$gte": dates[0]},
            }, {"captain_id": 1, "start_date": 1, "end_date": 1}).to_list(length=500)
            for v in upcoming:
                for c in v["captains"]:
                    if any(lv["captain_id"] == c["id"] and lv["start_date"] <= v["date"] <= lv["end_date"] for lv in leaves):
                        out.append({"kind": "captain_leave", "date": v["date"], "visit_ids": [v["id"]],
                                    "message": f"{c['name'] or 'A captain'} is on leave on {v['date_label']} ({v['society_name']})."})
        # Slots that can't take the planned visits.
        planned: dict[tuple[str, str], int] = {}
        for v in upcoming:
            if v["status"] != "planned":
                continue
            for a in v["allocations"]:
                if a.get("slot_key"):
                    planned[(v["date"], a["slot_key"])] = planned.get((v["date"], a["slot_key"]), 0) + 1
        if planned:
            room = await self._slot_room(center_id, sorted({d for d, _ in planned}))
            for (day, key), need in sorted(planned.items()):
                left = room.get((day, key))
                if left is not None and need > left:
                    out.append({"kind": "slot_full", "date": day, "visit_ids": [],
                                "message": f"{format_slot_12h(key)} on {date_label(day)} has room for {left} more booking{'s' if left != 1 else ''}, the schedule needs {need} — raise the slot capacity or move a visit."})
        for v in upcoming:
            if v["overflow"]:
                n = sum(len(u["cars"]) for u in v["overflow"])
                out.append({"kind": "overflow", "date": v["date"], "visit_ids": [v["id"]],
                            "message": f"{v['society_name']} on {v['date_label']}: {n} car{'s' if n != 1 else ''} due but not fitting — add a captain or washes per captain."})
        return out

    async def _slot_room(self, center_id: str, dates: list[str]) -> dict[tuple[str, str], int]:
        """Read-only: bookings each (date, slot) can still take. A slot not
        touched yet starts at the engine's own default
        (BookingService._slot_capacity_defaults); one whose capacity was
        never configured takes NO bookings, so it has 0 room (fail closed —
        it used to count as 999 free seats)."""
        from app.services.booking_policy_service import BookingPolicyService
        from app.services.booking_service import BookingService

        center = await self._center(center_id)
        policy = await BookingPolicyService(self.db).get_policy()
        slots = await self._slots(center)
        docs = await self.db.slot_capacity.find({"service_center_id": center_id, "date": {"$in": dates}}).to_list(length=500)
        by = {(d["date"], d["slot_key"]): d for d in docs}
        out = {}
        engine = BookingService(self.db)
        for day in dates:
            untouched = [key for key in slots if (day, key) not in by]
            defaults = await engine._slot_capacity_defaults(center, day, untouched, policy) if untouched else {}
            for key in slots:
                doc = by.get((day, key))
                if doc:
                    cap = 0 if doc.get("is_closed") else int(doc.get("capacity") or 0)
                    out[(day, key)] = max(0, cap - int(doc.get("booked_count") or 0) - int(doc.get("held_count") or 0))
                else:
                    out[(day, key)] = int(defaults.get(key) or 0)
        return out

    # ------------------------------------------------------------------
    # Rotation across a center's societies
    # ------------------------------------------------------------------

    async def build_rotation(self, center_id: str, payload, actor_id: str) -> dict:
        center = await self._center(center_id)
        start = self._check_date(payload.start_date)
        oids = [ObjectId(i) for i in payload.society_ids if ObjectId.is_valid(i)]
        found = {str(s["_id"]): s for s in await self.db.societies.find(
            {"_id": {"$in": oids}, "service_center_id": center_id, "is_deleted": {"$ne": True}}
        ).to_list(length=len(oids))}
        if len(found) != len(payload.society_ids):
            raise BadRequestException("Pick societies of this service center.")
        slots = await self._slots(center)
        keys = self._check_slots(slots, payload.slot_keys)
        await self._check_captains(center_id, payload.captain_ids)
        patterns = rotation_patterns(len(payload.society_ids), payload.weekdays, start)
        preview = []
        for sid, pattern in zip(payload.society_ids, patterns):
            firsts = pattern_dates(pattern, start, start + timedelta(weeks=pattern["interval_weeks"] * 2))
            preview.append({"society_id": sid, "society_name": found[sid].get("name"), "pattern": pattern, "label": pattern_label(pattern),
                            "first_dates": [d.isoformat() for d in firsts[:2]], "first_labels": [date_label(d) for d in firsts[:2]]})
        if payload.dry_run:
            return {"preview": preview, "created": 0}
        from app.schemas.society_schedule_schema import RulePattern, SocietyRuleRequest

        created = 0
        for sid, pattern in zip(payload.society_ids, patterns):
            society = found[sid]
            # A rotation replaces each society's existing visit-day rules.
            for old in await self.db.society_schedule_rules.find(
                {"society_id": sid, "kind": "society", "is_active": True, "is_deleted": {"$ne": True}}
            ).to_list(length=50):
                await self.delete_rule(old, actor_id)
            await self.create_rule(society, SocietyRuleRequest(
                pattern=RulePattern(**pattern), slot_keys=keys, captain_ids=payload.captain_ids,
                washes_per_captain=payload.washes_per_captain, start_date=start.isoformat(),
            ), "society", actor_id)
            created += 1
        return {"preview": preview, "created": created}

    # ------------------------------------------------------------------
    # Single-occurrence changes
    # ------------------------------------------------------------------

    async def create_visit(self, society: dict, payload, actor_id: str) -> dict:
        day = self._check_date(payload.date)
        center = await self._center(society.get("service_center_id"))
        slots = await self._slots(center)
        keys = self._check_slots(slots, payload.slot_keys)
        await self._check_captains(society["service_center_id"], payload.captain_ids)
        now = now_ist()
        visit = await self.visits.create({
            "society_id": str(society["_id"]), "service_center_id": society.get("service_center_id"), "kind": "society",
            "rule_id": None, "rule_date": None, "date": day.isoformat(), "slot_keys": keys, "captain_ids": list(payload.captain_ids),
            "washes_per_captain": int(payload.washes_per_captain), "enrollment_id": None, "customer_id": None, "subscription_ids": [],
            "excluded_subscription_ids": [], "status": "planned", "allocations": [], "overflow": [],
            "changes": [{"at": now, "by": actor_id, "what": "added"}], "notes": payload.notes, "created_by": actor_id,
        })
        return {"id": str(visit["_id"]), "date": visit["date"]}

    async def _log(self, visit_id: str, actor_id: str, what: str, extra: dict | None = None) -> None:
        await self.db.society_visits.update_one(
            {"_id": ObjectId(visit_id)},
            {"$push": {"changes": {"$each": [{"at": now_ist(), "by": actor_id, "what": what, **(extra or {})}], "$slice": -20}}},
        )

    async def update_visit(self, visit: dict, payload, user) -> dict:
        vid = str(visit["_id"])
        data = payload.model_dump(exclude_unset=True)
        status = visit.get("status")
        if status in ("skipped", "cancelled"):
            raise BadRequestException("Restore this day first, then change it.")
        if status == "generating":
            raise BadRequestException("This day is being booked right now — try again in a minute.")
        if _d(visit["date"]) <= today_ist():
            raise BadRequestException("This day has already started — change the bookings from the queue.")
        center = await self._center(visit.get("service_center_id"))
        slots = await self._slots(center)
        update: dict = {}
        notes: list[str] = []
        warnings: list[str] = []
        if "notes" in data:
            update["notes"] = data["notes"]
        if data.get("slot_keys") is not None:
            keys = self._check_slots(slots, data["slot_keys"])
            if visit.get("kind") == "resident":
                keys = keys[:1]
            if keys != list(visit.get("slot_keys") or []):
                if status in GENERATED and not data.get("date"):
                    raise BadRequestException("Already booked — move the day (it re-plans) or skip it to change the slots.")
                update["slot_keys"] = keys
                notes.append("slots")
        if data.get("washes_per_captain") is not None and int(data["washes_per_captain"]) != int(visit.get("washes_per_captain") or 0):
            if status in GENERATED and not data.get("date"):
                raise BadRequestException("Already booked — move the day (it re-plans) or skip it to change the washes per captain.")
            update["washes_per_captain"] = int(data["washes_per_captain"])
            notes.append("washes per captain")
        new_captains = None
        if data.get("captain_ids") is not None:
            await self._check_captains(visit["service_center_id"], data["captain_ids"])
            ids = list(dict.fromkeys(data["captain_ids"]))
            if visit.get("kind") == "resident":
                ids = ids[:1]
            if ids != list(visit.get("captain_ids") or []):
                update["captain_ids"] = ids
                new_captains = ids
                notes.append("captains")
        moved_to = None
        if data.get("date") and data["date"] != visit["date"]:
            moved_to = self._check_date(data["date"]).isoformat()
            if status in GENERATED:
                warnings += await self._cancel_visit_bookings(vid, user, "Society visit moved to another day")
                update.update(status="planned", allocations=[], overflow=[], generated_at=None)
            update["date"] = moved_to
            update["moved_from"] = visit["date"]
            notes.append(f"moved {visit['date']} → {moved_to}")
        elif new_captains is not None and status in GENERATED:
            warnings += await self._reassign_visit(visit, new_captains, user)
        if update:
            await self.visits.update_by_id(vid, update)
            await self._log(vid, user.id, "changed " + ", ".join(notes) if notes else "changed")
        return {"id": vid, "warnings": warnings}

    async def skip_visit(self, visit: dict, user, reason: str = "Society visit skipped") -> dict:
        vid = str(visit["_id"])
        if visit.get("status") in ("skipped", "cancelled"):
            return {"id": vid, "warnings": []}
        if visit.get("status") == "generating":
            raise BadRequestException("This day is being booked right now — try again in a minute.")
        if _d(visit["date"]) < today_ist():
            raise BadRequestException("This day has passed.")
        warnings = await self._cancel_visit_bookings(vid, user, reason) if visit.get("status") in GENERATED else []
        await self.visits.update_by_id(vid, {"status": "skipped", "skipped_at": now_ist()})
        await self._log(vid, user.id, "skipped")
        return {"id": vid, "warnings": warnings}

    async def restore_visit(self, visit: dict, user) -> dict:
        vid = str(visit["_id"])
        if visit.get("status") != "skipped":
            raise BadRequestException("Only a skipped day can be restored.")
        self._check_date(visit["date"])
        await self.visits.update_by_id(vid, {"status": "planned", "allocations": [], "overflow": [], "generated_at": None})
        await self._log(vid, user.id, "restored")
        return {"id": vid, "warnings": []}

    async def delete_visit(self, visit: dict, user) -> dict:
        if visit.get("rule_id"):
            raise BadRequestException("This day comes from a repeat rule — skip it instead.")
        if visit.get("status") in ("booked", "partial", "generating") or any(a.get("booking_ids") and a.get("status") == "booked" for a in visit.get("allocations") or []):
            raise BadRequestException("Already booked — skip it (cancels the bookings) instead.")
        await self.visits.update_by_id(str(visit["_id"]), {"is_deleted": True, "status": "cancelled"})
        return {"id": str(visit["_id"])}

    async def exclude(self, visit: dict, sub_ids: list[str], excluded: bool, user) -> dict:
        vid = str(visit["_id"])
        if visit.get("status") in ("skipped", "cancelled", "generating") or _d(visit["date"]) <= today_ist():
            raise BadRequestException("This day can't be changed now.")
        current = set(visit.get("excluded_subscription_ids") or [])
        changed = (set(sub_ids) - current) if excluded else (set(sub_ids) & current)
        current = current | set(sub_ids) if excluded else current - set(sub_ids)
        warnings: list[str] = []
        if excluded and visit.get("status") in GENERATED:
            warnings += await self._cancel_visit_bookings(vid, user, "Taken off this society visit", sub_ids=list(sub_ids))
            allocations = []
            for a in visit.get("allocations") or []:
                if set(a.get("sub_ids") or []) <= set(sub_ids):
                    a = {**a, "status": "cancelled"}
                allocations.append(a)
            await self.visits.update_by_id(vid, {"allocations": allocations})
        await self.visits.update_by_id(vid, {"excluded_subscription_ids": sorted(current)})
        if changed:
            await self._log(vid, user.id, ("took off " if excluded else "put back ") + f"{len(changed)} car{'s' if len(changed) != 1 else ''}")
        return {"id": vid, "warnings": warnings}

    async def _visit_bookings(self, visit_id: str, sub_ids: list[str] | None = None) -> list[dict]:
        query: dict = {"society_visit_id": visit_id, "is_deleted": {"$ne": True}}
        if sub_ids:
            query["subscription_id"] = {"$in": sub_ids}
        return await self.db.bookings.find(query).to_list(length=200)

    async def _cancel_visit_bookings(self, visit_id: str, user, reason: str, sub_ids: list[str] | None = None) -> list[str]:
        """Cancel the bookings a visit made (each refunds its premium wash
        through the normal cancel path). Jobs already under way are left
        alone and reported."""
        from app.schemas.booking_schema import BookingCancelRequest
        from app.services.booking_service import BookingService

        service = BookingService(self.db)
        warnings: list[str] = []
        rows = await self._visit_bookings(visit_id, sub_ids)
        groups: dict[str, list[dict]] = {}
        singles: list[dict] = []
        for b in rows:
            if b.get("status") not in CANCELLABLE:
                if b.get("status") not in ("cancelled", "completed"):
                    warnings.append(f"{b.get('booking_number')} is already under way — not cancelled.")
                continue
            if b.get("booking_group_id") and not sub_ids:
                groups.setdefault(b["booking_group_id"], []).append(b)
            else:
                singles.append(b)
        role, center = (user.role, user.service_center_id) if user else ("admin", None)
        actor = user.id if user else SYSTEM_ACTOR
        for gid in groups:
            try:
                await service.cancel_booking_group(gid, BookingCancelRequest(reason=reason), actor, role, center)
            except AppException as exc:
                warnings.append(exc.message)
        for b in singles:
            try:
                await service.cancel_booking(str(b["_id"]), BookingCancelRequest(reason=reason), actor, role, center)
            except AppException as exc:
                warnings.append(f"{b.get('booking_number')}: {exc.message}")
        return warnings

    async def _reassign_visit(self, visit: dict, captains: list[str], user) -> list[str]:
        """Booked day, new captains: each resident's booking moves to the
        captain now in its lane."""
        from app.schemas.booking_schema import BookingAssignCaptainRequest, ReassignCaptainRequest
        from app.services.booking_service import BookingService

        if not captains:
            return ["No captain picked — the bookings keep their captains."]
        service = BookingService(self.db)
        warnings = []
        for a in visit.get("allocations") or []:
            if a.get("status") != "booked" or not a.get("booking_ids"):
                continue
            target = captains[int(a.get("lane") or 0) % len(captains)]
            lead = await self.db.bookings.find_one({"_id": ObjectId(a["booking_ids"][0])})
            if not lead or lead.get("captain_id") == target or lead.get("status") not in ("pending", "assigned", "rescheduled"):
                continue
            try:
                if lead.get("status") == "assigned":
                    await service.reassign_captain(str(lead["_id"]), ReassignCaptainRequest(captain_id=target), user.id, user.role, user.service_center_id)
                else:
                    await service.assign_captain(str(lead["_id"]), BookingAssignCaptainRequest(captain_id=target), user.id, user.role, user.service_center_id)
                a["captain_id"] = target
            except AppException as exc:
                warnings.append(f"{lead.get('booking_number')}: {exc.message}")
        await self.visits.update_by_id(str(visit["_id"]), {"allocations": visit.get("allocations") or []})
        return warnings

    # ------------------------------------------------------------------
    # Generation: visit day -> real bookings
    # ------------------------------------------------------------------

    async def generate(self, visit_id: str, *, actor_id: str = SYSTEM_ACTOR, retry: bool = False) -> dict | None:
        """Claim the visit (one generator at a time), allocate, book. Returns
        the updated visit, or None when someone else holds it / it isn't due."""
        allowed = ["planned"] + (list(GENERATED) if retry else [])
        claimed = await self.db.society_visits.find_one_and_update(
            {"_id": ObjectId(visit_id), "status": {"$in": allowed}, "is_deleted": {"$ne": True}},
            {"$set": {"status": "generating", "generating_at": now_ist()}},
            return_document=ReturnDocument.BEFORE,
        )
        if not claimed:
            return None
        # Two visit days of one society on one date (a rule day + an added
        # day, or a moved one) must not project the same cars at once —
        # each would book them. One generator per (society, date): the
        # other hands its visit back and the next pass sees these bookings.
        lock = await self._lock_day(claimed["society_id"], claimed["date"])
        if not lock:
            await self.db.society_visits.update_one(
                {"_id": claimed["_id"], "status": "generating"}, {"$set": {"status": claimed.get("status") or "planned"}},
            )
            return None
        try:
            return await self._generate_claimed(claimed, actor_id)
        except Exception as exc:
            logger.exception("Society visit %s could not be booked", visit_id)
            # A day that blew up is parked as failed (shown with its error and
            # "Book now" to retry) — handing it back as planned made the
            # sweep pick the same broken days first on every pass, forever,
            # and no later day ever got booked.
            await self.db.society_visits.update_one(
                {"_id": claimed["_id"], "status": "generating"},
                {"$set": {"status": claimed.get("status") if claimed.get("status") in GENERATED else "failed",
                          "generation_error": str(exc)[:300] or "Couldn't book this day — try again."}},
            )
            raise
        finally:
            await self.db.society_generation_locks.delete_one({"_id": f"{claimed['society_id']}:{claimed['date']}", "token": lock})

    async def _lock_day(self, society_id: str, day: str) -> str | None:
        """Claims (society, date) for one generator; a holder that died
        is taken over after STALE_CLAIM. Returns the claim token or None."""
        key = f"{society_id}:{day}"
        token = secrets.token_hex(8)
        now = now_ist()
        try:
            await self.db.society_generation_locks.insert_one({"_id": key, "token": token, "locked_at": now})
            return token
        except DuplicateKeyError:
            taken = await self.db.society_generation_locks.find_one_and_update(
                {"_id": key, "locked_at": {"$lt": now - STALE_CLAIM}}, {"$set": {"token": token, "locked_at": now}},
            )
            return token if taken else None

    async def _generate_claimed(self, visit: dict, actor_id: str) -> dict:
        vid = str(visit["_id"])
        day = _d(visit["date"])
        society = await self.db.societies.find_one({"_id": ObjectId(visit["society_id"])})
        if not society or society.get("is_deleted") or society.get("is_active") is False:
            await self.visits.update_by_id(vid, {"status": "failed", "generation_error": "This society is switched off."})
            return await self.visits.find_by_id(vid)
        center = await self._center(society.get("service_center_id"))
        slots = await self._slots(center)
        policy = await self._policy()
        buffer = int(policy.get("captain_travel_buffer_minutes", 15))
        kept = [a for a in visit.get("allocations") or [] if a.get("status") == "booked"]
        kept_ids = {b for a in kept for b in a.get("booking_ids") or []}
        # This visit's bookings that its record doesn't know about (a run
        # that died after booking): re-derived and adopted below.
        orphans = {
            str(b["_id"]) for b in await self.db.bookings.find(
                {"society_visit_id": vid, "status": {"$in": LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}, {"_id": 1}
            ).to_list(length=200)
        } - kept_ids
        ctx = await self._load([society], day, see_through=orphans)
        sim = self._project(ctx, visit["society_id"], slots, buffer, treat_planned=vid)
        result = sim.get(vid) or {"placed": [], "overflow": []}
        allocations = list(kept)
        for unit in result["placed"]:
            allocations.append(await self._book_unit(visit, society, unit, actor_id))
        overflow = [{k: u.get(k) for k in ("enrollment_id", "sub_ids", "cars", "flat", "reason")} for u in result.get("overflow") or []]
        booked = [a for a in allocations if a.get("status") == "booked"]
        failed = [a for a in allocations if a.get("status") == "failed"]
        status = "empty" if not allocations else ("booked" if not failed else ("failed" if not booked else "partial"))
        await self.visits.update_by_id(vid, {
            "status": status, "allocations": allocations, "overflow": overflow,
            "generated_at": now_ist(), "generation_error": failed[0].get("error") if failed else None,
        })
        await self._log(vid, actor_id, f"booked {sum(len(a['sub_ids']) for a in booked)} wash(es)" + (f", {len(failed)} failed" if failed else ""))
        new = [a for a in booked if a not in kept]
        if new:
            await self._announce(visit, society, new, failed)
        try:
            from app.services.audit_service import AuditService

            await AuditService(self.db).log_action(
                actor_id, "system" if actor_id == SYSTEM_ACTOR else "staff", "GENERATE_SOCIETY_VISIT", "society_visits", vid,
                {"booked": len(booked), "failed": len(failed)}, service_center_id=society.get("service_center_id"),
            )
        except Exception:  # noqa: BLE001 — the audit row is a courtesy
            logger.exception("Could not audit society visit generation")
        return await self.visits.find_by_id(vid)

    async def _book_unit(self, visit: dict, society: dict, unit: dict, actor_id: str) -> dict:
        """One resident's cars on this visit -> bookings (via book_premium)
        -> captain assigned at the planned start. Adopts bookings that
        already exist for these cars on this date (a re-run never books twice)."""
        from app.schemas.society_schema import PremiumBookingRequest

        day = _d(visit["date"])
        vid = str(visit["_id"])
        alloc = {
            "enrollment_id": unit.get("enrollment_id"), "customer_id": unit.get("customer_id"), "sub_ids": list(unit["sub_ids"]),
            "lane": unit.get("lane"), "captain_id": unit.get("captain_id"), "slot_key": unit.get("slot_key"),
            "start_hhmm": min_to_hhmm(unit["start"]) if isinstance(unit.get("start"), int) else None,
            "minutes": unit.get("minutes"), "flat": unit.get("flat"), "warning": unit.get("warning"),
            "booking_ids": [], "status": "booked", "error": None,
        }
        existing = await self.db.bookings.find(
            {"subscription_id": {"$in": alloc["sub_ids"]}, "status": {"$in": LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}
        ).to_list(length=50)
        adopted = {b["subscription_id"]: b for b in existing if _booking_day(b) == day}
        to_book = [s for s in alloc["sub_ids"] if s not in adopted]
        ids = [str(adopted[s]["_id"]) for s in alloc["sub_ids"] if s in adopted]
        if adopted and not to_book:
            lead = min(adopted.values(), key=lambda b: int(b.get("group_offset_minutes") or 0))
            alloc["slot_key"] = lead.get("scheduled_slot") or alloc["slot_key"]
            if lead.get("captain_id"):
                alloc["captain_id"] = lead["captain_id"]
                if lead.get("estimated_start_at"):
                    alloc["start_hhmm"] = from_stored(lead["estimated_start_at"]).strftime("%H:%M")
        if to_book:
            try:
                with muted_manager_new_booking_alerts():
                    result = await self.society.book_premium(
                        PremiumBookingRequest(
                            subscription_ids=to_book, scheduled_date=day.isoformat(), scheduled_slot=alloc["slot_key"],
                            notes=f"Scheduled society premium wash — {society.get('name')}, {unit.get('flat') or 'flat'}",
                        ),
                        actor_id=actor_id, actor_role="admin", actor_center_id=None, society_id=visit["society_id"],
                        # The schedule never runs into a granted extension —
                        # that time is for booking remaining washes by hand.
                        allow_extension=False,
                    )
                ids += [b["id"] for b in result["bookings"]]
            except Exception as exc:  # noqa: BLE001 — one resident's failure never sinks the whole day
                if not isinstance(exc, AppException):
                    logger.exception("Society visit %s: booking %s failed", vid, alloc["sub_ids"])
                alloc.update(status="failed", error=exc.message if isinstance(exc, AppException) else "Couldn't book this wash — try again.")
                if ids:
                    alloc["booking_ids"] = ids
                return alloc
        alloc["booking_ids"] = ids
        if ids:
            await self.db.bookings.update_many({"_id": {"$in": [ObjectId(i) for i in ids]}}, {"$set": {"society_visit_id": vid}})
        if alloc["captain_id"] and ids and alloc["start_hhmm"]:
            h, m = [int(p) for p in alloc["start_hhmm"].split(":")]
            try:
                await self._assign_quietly(ids, alloc["captain_id"], datetime.combine(day, time(h, m), tzinfo=IST), actor_id)
            except AppException as exc:
                alloc["warning"] = f"Captain not assigned — {exc.message}"
                alloc["captain_id"] = None
        return alloc

    async def _assign_quietly(self, booking_ids: list[str], captain_id: str, start: datetime, actor_id: str) -> None:
        """The planned captain on every car of one resident's booking, each
        car at its own staggered start (the visit's start + the car's
        offset), through the ordinary guarded assign path — without a "new
        job" ping per car (the captain gets one line for the whole day).
        All or nothing: a clash on car two puts car one back."""
        from app.schemas.booking_schema import BookingAssignCaptainRequest
        from app.services.booking_service import BookingService

        service = BookingService(self.db)
        cars = await self.db.bookings.find({"_id": {"$in": [ObjectId(i) for i in booking_ids]}}).sort("group_offset_minutes", 1).to_list(length=20)
        moved: list[dict] = []
        try:
            for car in cars:
                if car.get("status") not in ("pending", "rescheduled") or car.get("captain_id"):
                    continue
                at = start + timedelta(minutes=int(car.get("group_offset_minutes") or 0))
                await service.assign_captain(
                    str(car["_id"]), BookingAssignCaptainRequest(captain_id=captain_id, estimated_start_at=at),
                    actor_id, "admin", None, _group_pass=True, _announce=False,
                )
                moved.append(car)
        except AppException:
            if moved:
                await service._undo_group_assignment(moved, captain_id, actor_id)
            raise

    async def _announce(self, visit: dict, society: dict, booked: list[dict], failed: list[dict]) -> None:
        """One in-app line per person for the whole day — never one per car."""
        from app.services.notification_service import NotificationService

        washes = sum(len(a["sub_ids"]) for a in booked)
        when = date_label(visit["date"])
        tail = f" {len(failed)} could not be booked — open the schedule." if failed else ""
        await self.society._tell_managers(
            society, "Society visit booked",
            f"{society.get('name')} on {when}: {washes} premium wash{'es' if washes != 1 else ''} booked.{tail}",
        )
        per_captain: dict[str, list[dict]] = {}
        for a in booked:
            if a.get("captain_id"):
                per_captain.setdefault(a["captain_id"], []).append(a)
        notifications = NotificationService(self.db)
        for cid, mine in per_captain.items():
            n = sum(len(a["sub_ids"]) for a in mine)
            first = min((a.get("start_hhmm") for a in mine if a.get("start_hhmm")), default=None)
            try:
                await notifications.notify(
                    cid, f"Society visit — {society.get('name')}",
                    f"{n} premium wash{'es' if n != 1 else ''} on {when}" + (f", from {format_time_12h(first)}" if first else "") + ".",
                    send_whatsapp=False,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Could not tell captain %s about society visit %s", cid, visit.get("_id"))

    # ------------------------------------------------------------------
    # Reminder-loop sweep (bounded, indexed, idempotent)
    # ------------------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None, limit: int = 5, checkpoint=None) -> dict:
        now = now or now_ist()
        today = now.date()
        stats = {"recovered": 0, "materialized": 0, "generated": 0}
        recovered = await self.db.society_visits.update_many(
            {"status": "generating", "generating_at": {"$lt": now - STALE_CLAIM}}, {"$set": {"status": "planned"}}
        )
        stats["recovered"] = recovered.modified_count
        horizon = (today + timedelta(days=HORIZON_DAYS)).isoformat()
        rules = await self.db.society_schedule_rules.find({
            "is_active": True, "is_deleted": {"$ne": True},
            "$or": [{"materialized_until": None}, {"materialized_until": {"$lt": horizon}}],
        }).limit(50).to_list(length=50)
        for rule in rules:
            stats["materialized"] += await self.materialize(rule)
        lead = (await self.settings())["generate_days_ahead"]
        tomorrow = (today + timedelta(days=1)).isoformat()
        due = await self.db.society_visits.find(
            {"status": "planned", "date": {"$gte": tomorrow, "$lte": (today + timedelta(days=lead)).isoformat()}, "is_deleted": {"$ne": True}},
            {"_id": 1, "date": 1},
        ).sort("date", 1).limit(limit).to_list(length=limit)
        in_hours = GENERATE_HOURS[0] <= now.hour < GENERATE_HOURS[1]
        for v in due:
            if not in_hours and v["date"] != tomorrow:
                continue
            if checkpoint:
                await checkpoint()
            try:
                if await self.generate(str(v["_id"])):
                    stats["generated"] += 1
            except Exception:  # noqa: BLE001 — one bad visit never stops the rest
                logger.exception("Society visit generation failed for %s", v["_id"])
        return stats

    # ------------------------------------------------------------------
    # Captain (read-only)
    # ------------------------------------------------------------------

    async def captain_visits(self, captain_id: str, days: int = 7) -> dict:
        today = today_ist()
        end = today + timedelta(days=days - 1)
        center_id = await self.society.captain_center(captain_id)
        if not center_id:
            return {"date": today.isoformat(), "visits": []}
        rows = await self.db.society_visits.find({
            "$or": [{"captain_ids": captain_id}, {"allocations.captain_id": captain_id}],
            # A captain moved to another center stops seeing the old one's days.
            "service_center_id": center_id,
            "date": {"$gte": today.isoformat(), "$lte": end.isoformat()},
            "status": {"$in": list(OPEN)}, "is_deleted": {"$ne": True},
        }).sort("date", 1).to_list(length=50)
        return {"date": today.isoformat(), "visits": [await self._captain_card(v, captain_id) for v in rows]}

    async def captain_visit(self, captain_id: str, visit_id: str) -> dict:
        visit = await self.visits.find_by_id(visit_id) if ObjectId.is_valid(visit_id or "") else None
        if not visit or not (
            captain_id in (visit.get("captain_ids") or []) or any(a.get("captain_id") == captain_id for a in visit.get("allocations") or [])
        ):
            raise NotFoundException("Visit not found")
        center_id = await self.society.captain_center(captain_id)
        if not center_id or visit.get("service_center_id") != center_id:
            raise NotFoundException("Visit not found")
        return await self._captain_card(visit, captain_id)

    async def _captain_card(self, visit: dict, captain_id: str) -> dict:
        society = await self.db.societies.find_one({"_id": ObjectId(visit["society_id"])}) or {}
        center = await self._center(society.get("service_center_id") or visit.get("service_center_id"))
        slots = await self._slots(center)
        policy = await self._policy()
        day = _d(visit["date"])
        ctx = await self._load([society], max(day, today_ist()))
        sim = self._project(ctx, visit["society_id"], slots, int(policy.get("captain_travel_buffer_minutes", 15)))
        views = await self._visit_views([visit], ctx, sim, {visit["society_id"]: society}, await self.settings())
        view = views[0]
        booking_status = {}
        ids = [b for a in view["allocations"] for b in a["booking_ids"]]
        if ids:
            rows = await self.db.bookings.find(
                {"_id": {"$in": [ObjectId(i) for i in ids]}}, {"captain_id": 1, "status": 1, "estimated_start_at": 1}
            ).to_list(length=200)
            booking_status = {str(b["_id"]): b for b in rows}
        cars = []
        for a in view["allocations"]:
            if a["status"] in ("cancelled", "failed"):
                continue
            mine_lane = a.get("captain_id") == captain_id
            for i, c in enumerate(a["cars"]):
                bid = a["booking_ids"][i] if i < len(a["booking_ids"]) else None
                b = booking_status.get(bid or "") or {}
                # Each car of a multi-car resident starts after the one before.
                start_label = a.get("start_label")
                if b.get("estimated_start_at"):
                    start_label = format_time_12h(from_stored(b["estimated_start_at"]).strftime("%H:%M"))
                cars.append({
                    "plate": c.get("plate"), "vehicle_type_name": c.get("vehicle_type_name"), "flat": a.get("flat"),
                    "service_name": c.get("service_name"), "start_label": start_label, "slot_label": a.get("slot_label"),
                    "booking_id": bid if b.get("captain_id") == captain_id else None,
                    "status": b.get("status") or a["status"], "mine": mine_lane or b.get("captain_id") == captain_id,
                    "captain_name": a.get("captain_name"),
                })
        mine = [c for c in cars if c["mine"]]
        return {
            "id": view["id"], "society_id": visit["society_id"], "society_name": society.get("name"),
            "address": ", ".join(p for p in [society.get("address_line"), society.get("area")] if p),
            "latitude": society.get("latitude"), "longitude": society.get("longitude"),
            "date": view["date"], "date_label": view["date_label"], "kind": view["kind"],
            "window_label": view["window_label"], "status": view["status"], "generate_on": view["generate_on"],
            "my_washes": len(mine), "total_washes": len(cars),
            "captains": view["captains"],
            "cars": mine or cars,
        }

    # ------------------------------------------------------------------
    # Resident
    # ------------------------------------------------------------------

    async def my_schedule(self, customer_id: str, society_id: str | None = None) -> dict:
        now = now_ist()
        query: dict = {"customer_id": customer_id, "society_id": {"$nin": [None, ""]}, "status": "active", "end_date": {"$gt": now}, "is_deleted": {"$ne": True}}
        if society_id:
            query["society_id"] = society_id
        subs = await self.db.user_subscriptions.find(query, {"society_id": 1}).to_list(length=50)
        mine = {str(s["_id"]) for s in subs}
        sids = sorted({s["society_id"] for s in subs})
        settings = await self.settings()
        if not sids:
            return {"items": [], "next_by_subscription": {}, "requests": [], "slots_by_society": {}, "lead_days": settings["generate_days_ahead"]}
        oids = [ObjectId(s) for s in sids if ObjectId.is_valid(s)]
        societies = await self.db.societies.find({"_id": {"$in": oids}}).to_list(length=len(oids))
        today = today_ist()
        through = today + timedelta(days=HORIZON_DAYS)
        await self.ensure_materialized(sids, through)
        policy = await self._policy()
        buffer = int(policy.get("captain_travel_buffer_minutes", 15))
        items = []
        slots_by_society: dict[str, list[dict]] = {}
        for society in societies:
            sid = str(society["_id"])
            center = await self._center(society.get("service_center_id"))
            slots = await self._slots(center)
            slots_by_society[sid] = [{"key": k, "label": format_slot_12h(k)} for k in sorted(slots, key=lambda k: slots[k].start)]
            ctx = await self._load([society], through)
            sim = self._project(ctx, sid, slots, buffer)
            for v in ctx["visits"]:
                if _d(v["date"]) < today or v.get("status") not in OPEN:
                    continue
                units = v.get("allocations") if v.get("status") in GENERATED else (sim.get(str(v["_id"])) or {}).get("placed")
                for u in units or []:
                    cars = [s for s in u.get("sub_ids") or [] if s in mine]
                    if not cars or u.get("status") in ("failed", "cancelled"):
                        continue
                    start = u.get("start")
                    start_hhmm = min_to_hhmm(start) if isinstance(start, int) else u.get("start_hhmm")
                    items.append({
                        "visit_id": str(v["_id"]), "society_id": sid, "society_name": society.get("name"),
                        "date": v["date"], "date_label": date_label(v["date"]), "kind": v.get("kind"),
                        "slot_key": u.get("slot_key"), "slot_label": format_slot_12h(u["slot_key"]) if u.get("slot_key") else None,
                        "start_label": format_time_12h(start_hhmm) if start_hhmm else None,
                        "status": "booked" if v.get("status") in GENERATED else "planned",
                        "cars": [
                            {"sub_id": s, "plate": (ctx["info"].get(s) or {}).get("plate"), "vehicle_type_name": (ctx["info"].get(s) or {}).get("vehicle_type_name") or None}
                            for s in cars
                        ],
                        "can_request": _d(v["date"]) > today,
                    })
        # Premium washes booked by hand (hub / manager), not from a visit day.
        society_of = {str(x["_id"]): x["society_id"] for x in subs}
        names = {str(x["_id"]): x.get("name") for x in societies}
        plates, car_types = {}, {}
        type_names = await self.society._type_names()
        for e in await self.db.society_enrollments.find({"customer_id": customer_id, "society_id": {"$in": sids}}, {"cars": 1}).to_list(length=20):
            plates.update({c.get("subscription_id"): c.get("registration_number") for c in e.get("cars") or []})
            car_types.update({c.get("subscription_id"): type_names.get(c.get("vehicle_type") or "") or None for c in e.get("cars") or []})
        bookings = await self.db.bookings.find(
            {"subscription_id": {"$in": list(mine)}, "status": {"$in": LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True},
             "scheduled_date": {"$gte": datetime.combine(today, time())}},
            {"subscription_id": 1, "scheduled_date": 1, "scheduled_slot": 1, "society_visit_id": 1},
        ).to_list(length=100)
        for b in bookings:
            day = _booking_day(b)
            if not day or b.get("society_visit_id"):
                continue
            sid = society_of.get(b["subscription_id"])
            items.append({
                "visit_id": None, "booking_id": str(b["_id"]), "society_id": sid, "society_name": names.get(sid),
                "date": day.isoformat(), "date_label": date_label(day), "kind": "booking",
                "slot_key": b.get("scheduled_slot"), "slot_label": format_slot_12h(b.get("scheduled_slot")), "start_label": None,
                "status": "booked", "can_request": False,
                "cars": [{"sub_id": b["subscription_id"], "plate": plates.get(b["subscription_id"]), "vehicle_type_name": car_types.get(b["subscription_id"])}],
            })
        items.sort(key=lambda i: (i["date"], i.get("slot_key") or ""))
        # Next premium wash per car (scheduled or booked).
        nxt: dict[str, dict] = {}
        for it in items:
            for c in it["cars"]:
                nxt.setdefault(c["sub_id"], {k: it[k] for k in ("date", "date_label", "slot_key", "slot_label", "status")})
        reqs = await self.db.society_schedule_requests.find({"customer_id": customer_id}).sort("created_at", -1).to_list(length=20)
        return {
            "items": items[:30], "next_by_subscription": nxt, "requests": await self.request_views(reqs),
            "slots_by_society": slots_by_society, "lead_days": settings["generate_days_ahead"],
        }

    # ------------------------------------------------------------------
    # Change requests
    # ------------------------------------------------------------------

    async def create_request(self, customer_id: str, payload) -> dict:
        visit = await self.visits.find_by_id(payload.visit_id) if ObjectId.is_valid(payload.visit_id) else None
        if not visit or visit.get("status") not in OPEN:
            raise NotFoundException("Scheduled wash not found")
        if _d(visit["date"]) <= today_ist():
            raise BadRequestException("This wash is today or past — please call your society manager.")
        mine = await self.my_schedule(customer_id, visit["society_id"])
        item = next((i for i in mine["items"] if i["visit_id"] == str(visit["_id"])), None)
        if not item:
            raise NotFoundException("Scheduled wash not found")
        if payload.kind == "move":
            new = self._check_date(payload.preferred_date, max_days=30)
            if payload.preferred_slot:
                center = await self._center(visit.get("service_center_id"))
                self._check_slots(await self._slots(center), [payload.preferred_slot])
            if new.isoformat() == visit["date"] and not payload.preferred_slot:
                raise BadRequestException("Pick a different date.")
        sub_ids = [c["sub_id"] for c in item["cars"]]
        sub = await self.db.user_subscriptions.find_one({"_id": ObjectId(sub_ids[0])}, {"enrollment_id": 1})
        try:
            req = await self.requests.create({
                "visit_id": str(visit["_id"]), "society_id": visit["society_id"], "service_center_id": visit.get("service_center_id"),
                "customer_id": customer_id, "enrollment_id": (sub or {}).get("enrollment_id"), "subscription_ids": sub_ids,
                "visit_date": visit["date"], "slot_key": item.get("slot_key"), "kind": payload.kind,
                "preferred_date": payload.preferred_date if payload.kind == "move" else None,
                "preferred_slot": payload.preferred_slot if payload.kind == "move" else None,
                "note": payload.note, "status": "pending",
            })
        except DuplicateKeyError as exc:
            raise BadRequestException("You've already asked to change this wash — your manager will reply soon.") from exc
        society = await self.db.societies.find_one({"_id": ObjectId(visit["society_id"])}) or {}
        enrollment = await self.db.society_enrollments.find_one({"_id": ObjectId(req["enrollment_id"])}) if req.get("enrollment_id") and ObjectId.is_valid(req["enrollment_id"]) else None
        who = f"{(enrollment or {}).get('resident_name') or 'A resident'} ({(enrollment or {}).get('flat') or '—'})"
        what = "skip" if payload.kind == "skip" else f"move to {date_label(payload.preferred_date)}"
        await self.society._tell_managers(society, "Premium wash change request", f"{who} asks to {what} their premium wash on {date_label(visit['date'])} at {society.get('name')}.")
        return (await self.request_views([req]))[0]

    async def request_views(self, rows: list[dict]) -> list[dict]:
        enrollments = await self.society._enrollments_by_id([r.get("enrollment_id") for r in rows])
        societies = {}
        sids = {r.get("society_id") for r in rows if r.get("society_id") and ObjectId.is_valid(r["society_id"])}
        if sids:
            societies = {str(s["_id"]): s.get("name") for s in await self.db.societies.find({"_id": {"$in": [ObjectId(s) for s in sids]}}, {"name": 1}).to_list(length=len(sids))}
        type_names = await self.society._type_names() if rows else {}
        out = []
        for r in rows:
            e = enrollments.get(r.get("enrollment_id") or "") or {}
            plates = {c.get("subscription_id"): c.get("registration_number") for c in e.get("cars") or []}
            types = {c.get("subscription_id"): type_names.get(c.get("vehicle_type") or "") or None for c in e.get("cars") or []}
            out.append({
                "id": str(r["_id"]), "visit_id": r.get("visit_id"), "society_id": r.get("society_id"), "society_name": societies.get(r.get("society_id") or ""),
                "resident_name": e.get("resident_name"), "flat": e.get("flat"),
                "plates": [plates.get(s) for s in r.get("subscription_ids") or [] if plates.get(s)],
                "cars": [{"plate": plates.get(s), "vehicle_type_name": types.get(s)} for s in r.get("subscription_ids") or [] if plates.get(s)],
                "subscription_ids": r.get("subscription_ids") or [],
                "visit_date": r.get("visit_date"), "visit_date_label": date_label(r["visit_date"]) if r.get("visit_date") else None,
                "slot_label": format_slot_12h(r["slot_key"]) if r.get("slot_key") else None,
                "kind": r.get("kind"), "preferred_date": r.get("preferred_date"),
                "preferred_date_label": date_label(r["preferred_date"]) if r.get("preferred_date") else None,
                "preferred_slot": r.get("preferred_slot"), "preferred_slot_label": format_slot_12h(r["preferred_slot"]) if r.get("preferred_slot") else None,
                "note": r.get("note"), "status": r.get("status"), "resolution_note": r.get("resolution_note"),
                "created_at": from_stored(r["created_at"]).isoformat() if r.get("created_at") else None,
            })
        return out

    async def list_requests(self, center_id: str | None, status: str | None = "pending") -> list[dict]:
        query: dict = {}
        if center_id:
            query["service_center_id"] = center_id
        if status:
            query["status"] = status
        rows = await self.db.society_schedule_requests.find(query).sort("created_at", -1).to_list(length=200)
        return await self.request_views(rows)

    async def approve_request(self, req: dict, payload, user) -> dict:
        if req.get("status") != "pending":
            raise BadRequestException("This request was already answered.")
        visit = await self.visits.find_by_id(req["visit_id"]) if ObjectId.is_valid(req.get("visit_id") or "") else None
        if not visit or _d(visit["date"]) <= today_ist():
            raise BadRequestException("That wash is today or past now — decline it and call the resident.")
        sub_ids = list(req.get("subscription_ids") or [])
        if req.get("kind") == "move":
            # Validated BEFORE the old day is touched: a bad date / slot /
            # captain must leave the visit and the request exactly as they were.
            day = self._check_date(payload.date or req["preferred_date"])
            slot = payload.slot_key or req.get("preferred_slot") or req.get("slot_key") or (visit.get("slot_keys") or [None])[0]
            center = await self._center(visit.get("service_center_id"))
            self._check_slots(await self._slots(center), [slot])
            captain = payload.captain_id or (visit.get("captain_ids") or [None])[0]
            if captain:
                await self._check_captains(visit["service_center_id"], [captain])
        # Answered exactly once: claimed pending -> approved atomically, so a
        # double tap / two staff (or an approve racing a decline) can't skip
        # the day twice or create two moved visits. Put back if it fails.
        await self._answer_once(req, {"status": "approved", "resolution_note": payload.note, "resolved_by": user.id, "resolved_at": now_ist()})
        try:
            if visit.get("kind") == "resident" and visit.get("customer_id") == req["customer_id"] and set(visit.get("subscription_ids") or []) <= set(sub_ids):
                await self.skip_visit(visit, user, "Resident asked to change this premium wash")
            else:
                await self.exclude(visit, sub_ids, True, user)
            message = f"Your premium wash on {date_label(visit['date'])} is skipped."
            new_visit = None
            if req.get("kind") == "move":
                now = now_ist()
                new_visit = await self.visits.create({
                    "society_id": visit["society_id"], "service_center_id": visit.get("service_center_id"), "kind": "resident",
                    "rule_id": None, "rule_date": None, "date": day.isoformat(), "slot_keys": [slot],
                    "captain_ids": [captain] if captain else [], "washes_per_captain": 99,
                    "enrollment_id": req.get("enrollment_id"), "customer_id": req["customer_id"], "subscription_ids": sub_ids,
                    "excluded_subscription_ids": [], "status": "planned", "allocations": [], "overflow": [],
                    "changes": [{"at": now, "by": user.id, "what": f"moved from {visit['date']} (resident's request)"}],
                    "moved_from": visit["date"], "request_id": str(req["_id"]), "notes": req.get("note"), "created_by": user.id,
                })
                message = f"Your premium wash moved to {date_label(day)}, {format_slot_12h(slot)}."
        except BaseException:
            await self.requests.collection.update_one(
                {"_id": req["_id"], "status": "approved"}, {"$set": {"status": "pending", "resolved_by": None, "resolved_at": None}},
            )
            raise
        await self.requests.update_by_id(str(req["_id"]), {"new_visit_id": str(new_visit["_id"]) if new_visit else None})
        await self.society._tell_resident(req["customer_id"], "Premium wash updated", message + (f" {payload.note}" if payload.note else ""))
        return (await self.request_views([await self.requests.find_by_id(str(req["_id"]))]))[0]

    async def _answer_once(self, req: dict, fields: dict) -> None:
        claimed = await self.requests.collection.find_one_and_update(
            {"_id": req["_id"], "status": "pending", "is_deleted": {"$ne": True}}, {"$set": {**fields, "updated_at": now_ist()}},
        )
        if not claimed:
            raise BadRequestException("This request was already answered.")

    async def decline_request(self, req: dict, payload, user) -> dict:
        if req.get("status") != "pending":
            raise BadRequestException("This request was already answered.")
        await self._answer_once(req, {"status": "declined", "resolution_note": payload.note, "resolved_by": user.id, "resolved_at": now_ist()})
        when = date_label(req["visit_date"]) if req.get("visit_date") else "your scheduled day"
        await self.society._tell_resident(
            req["customer_id"], "Premium wash unchanged",
            f"Your premium wash stays on {when}." + (f" {payload.note}" if payload.note else ""),
        )
        return (await self.request_views([await self.requests.find_by_id(str(req["_id"]))]))[0]
