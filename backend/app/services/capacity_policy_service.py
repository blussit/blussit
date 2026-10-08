"""
Capacity policy — the single source of truth for a service center's
maximum daily bookings and per-slot distribution, WITH effective-dating
(Sections 1-3 of the BLUSSIT UX/capacity update). Deliberately a separate,
focused service rather than folded into the already-large BookingService —
matches the same "one clear responsibility per page/module" principle the
frontend redesign is built around.

Relationship to the existing slot_capacity/daily_capacity collections
(unchanged, still owned by BookingService): those are per-DATE reservation
counters ("how many of today's 09:00-12:00 spots are taken"), lazily
created the first time anyone reserves into (or an admin views/edits) that
exact date+slot. THIS service answers a different question — "what
capacity SHOULD a date+slot for this center resolve to" — and
BookingService consults it (via get_effective_policy) at lazy-init time.
Because that init is a one-time $setOnInsert, a date's docs can already
exist by the time a NEW policy is scheduled for that same date (most
commonly: today, under "Apply immediately") — _resync_from pushes the
new numbers into every already-existing doc the change governs (its date
up to the next scheduled change) right when the change is
scheduled/cancelled, so Overview and the customer-facing
availability view (which both read those same docs) never go stale. An
admin's per-date/per-slot override (BookingService.set_slot_capacity)
still always wins over the baseline policy for whatever date it was set
on — set_slot_capacity marks that doc `is_override`, and the resync
deliberately skips any doc carrying that flag — that's the "Capacity
overrides" the spec calls out as a distinct concept from the day's
baseline policy.
"""
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.repositories.capacity_policy_repository import CapacityPolicyRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.slot_capacity_repository import DailyCapacityRepository, SlotCapacityRepository
from app.utils.timezone import now_ist
from app.utils.serializers import serialize_doc, serialize_list


class CapacityPolicyService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = CapacityPolicyRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        # Only used to re-sync ALREADY-INITIALIZED slot_capacity/daily_capacity
        # docs when the policy governing their date changes — see
        # _resync_from below. Reservation/release itself stays owned
        # by BookingService; this service never reserves/releases a spot.
        self.slot_capacity_repo = SlotCapacityRepository(db)
        self.daily_capacity_repo = DailyCapacityRepository(db)

    async def _resolve_slots(self, center: dict) -> list[str]:
        # THE center slot list the booking side uses (center_slots: its own
        # hours and length, or the platform "Slot length") — a hard-coded
        # 180 / 08:00-20:00 here saved per-slot numbers under keys that
        # matched no real slot once those changed.
        from app.services.booking_policy_service import BookingPolicyService
        from app.services.booking_service import center_slots

        return [s["key"] for s in center_slots(center, await BookingPolicyService(self.db).get_policy())]

    def auto_distribute(self, max_bookings_per_day: int, slot_keys: list[str]) -> dict[str, int]:
        """Even split with the remainder absorbed by the LAST slot — same
        "remainder goes last" convention already used by generate_slots
        for the slot windows themselves, so the two stay visually
        consistent (e.g. a day with an odd remainder always lands on the
        last, usually-shortest, slot)."""
        if not slot_keys:
            return {}
        base = max_bookings_per_day // len(slot_keys)
        distribution = {key: base for key in slot_keys}
        remainder = max_bookings_per_day - base * len(slot_keys)
        distribution[slot_keys[-1]] += remainder
        return distribution

    async def _resync_from(self, service_center_id: str, effective_date: str, max_bookings_per_day: int | None, distribution: dict[str, int]) -> None:
        """A slot/day's capacity doc (owned by BookingService) is created
        lazily the FIRST time anyone touches that exact date — a booking, a
        public slot hold, an admin viewing the Overview page. From that
        moment on, get_or_init's $setOnInsert means its `capacity` field is
        frozen at whatever it started as; a policy change made afterwards
        never reaches back to update it on its own ("I changed the number on
        the policy page but Overview still shows the old one").

        A policy governs every date from its effective_date up to the next
        scheduled change, so every doc that already exists in THAT range is
        resynced right now — not just effective_date's own (the original
        resync left later dates on the old numbers: a cut from 4 to 1 still
        sold 4 tomorrow). Capacity only, never booked_count: no existing
        reservation is ever invalidated, a cut below what's already booked
        just stops new sales. And never a slot/day an admin explicitly
        overrode via set_slot_capacity — that override always wins over the
        baseline policy, by design."""
        following = await self.repo.find_next_scheduled(service_center_id, effective_date)
        window: dict = {"$gte": effective_date}
        if following:
            window["$lt"] = following["effective_date"]
        now = datetime.now(timezone.utc)
        for slot_key, capacity in distribution.items():
            await self.slot_capacity_repo.collection.update_many(
                {"service_center_id": service_center_id, "date": window, "slot_key": slot_key,
                 "is_override": {"$ne": True}, "capacity": {"$ne": capacity}},
                {"$set": {"capacity": capacity, "updated_at": now}},
            )
        if max_bookings_per_day is not None:
            await self.daily_capacity_repo.collection.update_many(
                {"service_center_id": service_center_id, "date": window,
                 "is_override": {"$ne": True}, "capacity": {"$ne": max_bookings_per_day}},
                {"$set": {"capacity": max_bookings_per_day, "updated_at": now}},
            )

    async def get_effective_policy(self, service_center_id: str, date_str: str) -> dict:
        """What SHOULD a not-yet-touched date+slot for this center start
        out with — the most recent scheduled change whose effective_date
        is <= date_str, or the center's legacy flat fields if no policy
        change has ever been scheduled (pre-existing centers keep working
        exactly as before this feature shipped)."""
        change = await self.repo.find_latest_effective(service_center_id, date_str)
        if change:
            return {"max_bookings_per_day": change["max_bookings_per_day"], "slot_distribution": change.get("slot_distribution") or {}}
        center = await self.center_repo.find_by_id(service_center_id)
        return {"max_bookings_per_day": (center or {}).get("max_bookings_per_day"), "slot_distribution": {}}

    async def overview(self, service_center_id: str) -> dict:
        """{current, scheduled} — what the Capacity page's header shows:
        today's active policy, and the next scheduled future change (if
        any) with its effective date, so admin can see both at a glance
        without hunting through history."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        today = now_ist().strftime("%Y-%m-%d")
        current = await self.get_effective_policy(service_center_id, today)
        scheduled = await self.repo.find_next_scheduled(service_center_id, today)
        return {
            "current": current,
            "scheduled": serialize_doc(scheduled) if scheduled else None,
            "slot_keys": await self._resolve_slots(center),
        }

    async def list_history(self, service_center_id: str) -> list[dict]:
        today = now_ist().strftime("%Y-%m-%d")
        items = await self.repo.list_history(service_center_id)
        results = []
        current = await self.repo.find_latest_effective(service_center_id, today)
        current_id = str(current["_id"]) if current else None
        for item in items:
            status = "scheduled" if item["effective_date"] > today else ("active" if str(item["_id"]) == current_id else "past")
            doc = serialize_doc(item)
            doc["status"] = status
            results.append(doc)
        return results

    async def schedule_change(
        self,
        service_center_id: str,
        effective_date: str,
        max_bookings_per_day: int,
        slot_distribution: dict[str, int] | None,
        actor_id: str,
        actor_role: str,
        actor_center_id: str | None,
        note: str | None = None,
    ) -> dict:
        """Creates (or, for a still-future date, edits in place — see the
        unique index on (service_center_id, effective_date)) a capacity
        policy change. Validates: max can't be negative, effective_date
        can't be edited once it's already active (past/today — that's
        immutable history, schedule a new later change instead), every
        slot_distribution key is a real slot for this center, and the
        distribution sums to EXACTLY max_bookings_per_day (Section 2: "The
        total slot capacity MUST equal the configured maximum daily
        capacity")."""
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            raise NotFoundException("Service center not found")
        if max_bookings_per_day < 0:
            raise BadRequestException("Maximum daily bookings can't be negative")

        today = now_ist().strftime("%Y-%m-%d")
        if effective_date < today:
            raise BadRequestException("Effective date can't be in the past")

        # A cancelled change used to stay behind as a soft-deleted row that
        # the unique (center, effective_date) index still counts — scheduling
        # that date again was a 500. Clear any such tombstone first.
        await self.repo.collection.delete_many(
            {"service_center_id": service_center_id, "effective_date": effective_date, "is_deleted": True}
        )
        existing = await self.repo.find_for_date(service_center_id, effective_date)
        slot_keys = await self._resolve_slots(center)
        distribution = dict(slot_distribution or {})
        if not distribution:
            distribution = self.auto_distribute(max_bookings_per_day, slot_keys)
        else:
            unknown = set(distribution.keys()) - set(slot_keys)
            if unknown:
                raise BadRequestException(f"Unknown slot(s) for this center: {', '.join(sorted(unknown))}")
            if any(v < 0 for v in distribution.values()):
                raise BadRequestException("Slot capacity can't be negative")
            total = sum(distribution.values())
            if total != max_bookings_per_day:
                raise BadRequestException(
                    f"Slot capacities must add up to exactly the daily maximum ({total} given, {max_bookings_per_day} expected)."
                )

        doc = {
            "service_center_id": service_center_id,
            "effective_date": effective_date,
            "max_bookings_per_day": max_bookings_per_day,
            "slot_distribution": distribution,
            "note": note,
            "created_by": actor_id,
        }
        # A record for this exact effective_date is always editable in
        # place here — including "today" (re-running "apply immediately"
        # later the same day updates the same record rather than creating
        # a second one for the same date, matching the unique index).
        # effective_date strictly BEFORE today can never reach this point:
        # the guard above already rejects any submitted effective_date <
        # today, and `existing` is looked up by that same submitted value —
        # so a genuinely past record is structurally unreachable here.
        # cancel_scheduled_change (looked up by id, not by date) is the
        # method that actually needs — and has — the "already active, no
        # longer touchable" guard, for a strictly future record someone
        # tries to cancel after it's already gone live.
        if existing:
            updated = await self.repo.update_by_id(str(existing["_id"]), doc)
            result = serialize_doc(updated)
        else:
            created = await self.repo.create(doc)
            result = serialize_doc(created)

        # This new/edited change is now the effective policy for
        # effective_date — but ONLY if it's actually the winning one for
        # that date (a later-created change with an earlier effective_date
        # could already be scheduled in between; get_effective_policy is
        # the single source of truth for "which policy wins", so defer to
        # it rather than assuming this one always does).
        current_for_date = await self.get_effective_policy(service_center_id, effective_date)
        if current_for_date["max_bookings_per_day"] == max_bookings_per_day:
            await self._resync_from(service_center_id, effective_date, max_bookings_per_day, distribution)
        return result

    async def cancel_scheduled_change(self, service_center_id: str, change_id: str, actor_id: str, actor_role: str, actor_center_id: str | None) -> None:
        ensure_own_center(actor_role, actor_center_id, service_center_id)
        change = await self.repo.find_by_id(change_id)
        if not change or change.get("service_center_id") != service_center_id:
            raise NotFoundException("Scheduled capacity change not found")
        today = now_ist().strftime("%Y-%m-%d")
        if change["effective_date"] <= today:
            raise BadRequestException("This capacity change is already active and can no longer be cancelled — schedule a new change instead.")
        await self.repo.soft_delete(change_id)

        # Symmetric with schedule_change: if that date's docs already exist
        # (e.g. a real booking was already made against it, or an admin
        # already previewed it under Overview), resync them back down to
        # whatever now resolves as effective now that this change is gone —
        # otherwise a cancelled change's numbers would keep "sticking" the
        # same way an applied one used to before this fix.
        # Every date the cancelled change would have governed (up to the
        # next scheduled one) reverts, not just its first day.
        center = await self.center_repo.find_by_id(service_center_id)
        reverted = await self.get_effective_policy(service_center_id, change["effective_date"])
        if center:
            distribution = reverted["slot_distribution"]
            if not distribution and reverted["max_bookings_per_day"] is not None:
                distribution = self.auto_distribute(reverted["max_bookings_per_day"], await self._resolve_slots(center))
            elif not distribution:
                # No policy before it at all: back to the center's own
                # flat per-slot default (what BookingService falls back to) —
                # and none configured means none (fail closed, never 999).
                default = int(center.get("default_slot_capacity") or 0)
                distribution = {key: default for key in await self._resolve_slots(center)}
            await self._resync_from(service_center_id, change["effective_date"], reverted["max_bookings_per_day"], distribution)

    # Kept for older callers.
    _auto_distribute = auto_distribute

    async def rekey_for_current_slots(self, service_center_id: str) -> dict | None:
        """After an allowed change to a center's slot length or hours
        (ServiceCenterService.update / the platform slot length — only
        possible while nothing live sits in the old slots, see
        BookingService.slot_change_blockers), the policy in effect today
        still names the OLD slot keys. Re-split its same daily maximum over
        the new slots, as today's change (edited in place if one exists),
        and push it into any counter docs already created. None when there
        was nothing to re-split."""
        center = await self.center_repo.find_by_id(service_center_id)
        if not center:
            return None
        today = now_ist().strftime("%Y-%m-%d")
        active = await self.repo.find_latest_effective(service_center_id, today)
        keys = await self._resolve_slots(center)
        if not active or not active.get("slot_distribution") or set(active["slot_distribution"]) == set(keys):
            return None
        maximum = int(active.get("max_bookings_per_day") or 0)
        doc = {
            "service_center_id": service_center_id,
            "effective_date": today,
            "max_bookings_per_day": maximum,
            "slot_distribution": self.auto_distribute(maximum, keys),
            "note": "Re-split over the new slots after a slot length / opening hours change",
            "created_by": "system",
        }
        existing = await self.repo.find_for_date(service_center_id, today)
        saved = await self.repo.update_by_id(str(existing["_id"]), doc) if existing else await self.repo.create(doc)
        await self._resync_from(service_center_id, today, maximum, doc["slot_distribution"])
        return serialize_doc(saved)
