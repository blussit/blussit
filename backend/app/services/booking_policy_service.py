"""
Booking policy — the admin-configurable rules that keep scheduling honest:

  - operating_start / operating_end: LEGACY, no longer used to generate or
    validate booking slots (every service center has its own real
    working_hours_start/end, which slots are always generated from — see
    app/utils/slots.py). Kept only so old callers/records referencing these
    keys don't break; do not wire new scheduling logic to these.
  - min_lead_minutes: LEGACY, superseded by slot_booking_cutoff_minutes for
    slot-based booking — a slot's own cutoff (its end minus the cutoff
    buffer) is what actually gates whether it can still be booked now.
  - slot_duration_minutes: how long each admin-generated booking slot is
    (default 180, i.e. 3 hours) — a service center may override this with
    its own `slot_duration_minutes`; this is the platform-wide fallback
    when a center doesn't. The final slot in a day absorbs whatever
    remainder doesn't divide evenly (see generate_slots).
  - slot_booking_cutoff_minutes: how close to a slot's END it can still be
    booked (default 30) — e.g. a 09:00-12:00 slot with this at 30 remains
    bookable until 11:30. Applies uniformly to every slot, derived from
    each slot's own end time, never hardcoded per-slot.
  - delay_tolerance_minutes: how far past a service's expected duration
    (summed from selected services) actual completion can run before it's
    flagged as a delay (default 20) — replaces the old hardcoded
    SERVICE_OVERRUN_MINUTES constant.
  - captain_travel_buffer_minutes: minutes blocked out before AND after a
    captain's booking window, during which they cannot be assigned another
    job — models real travel time between jobs (default 15).
  - photo_geofence_radius_m: how far a before/after photo's GPS can be from
    the booking address before it gets flagged for manager review, rather
    than blocked outright (default 300m — service addresses aren't
    pinpoint-accurate, so this stays generous).
  - late_start_grace_minutes: how long past a booking's expected start
    (estimated_start_at, or the slot end for the hard "severely late"
    boundary) a captain can still start before the penalty escalates.
  - captain_start_lockout_hours: how many hours past late_start_grace_minutes
    an ASSIGNED booking's captain can still be allowed to start it at all
    (default 4). Below this, a late start just costs a penalty (see
    LATE_START_PENALTY_PCT/SEVERE_LATE_START_PENALTY_PCT) but is still
    allowed — the captain simply got moving late. Past it, the booking is
    considered abandoned: start_heading is blocked outright and the
    automated sweep re-flags it as "captain_missed_window" (distinct from
    the earlier "captain_not_started" nudge, and NOT exempt from blocking)
    so a manager has to reschedule or reassign it before anyone can act on
    it again — a captain should never be able to "start" a job that's
    days stale with nothing but a bigger penalty label on it.
  - cancellation_fee_1_to_4h / cancellation_fee_under_1h /
    cancellation_fee_after_captain_left: the late-cancellation charge (₹)
    for a customer-requested cancellation 1–4 hours before the slot, under
    an hour before (or after the slot started), and after the captain left
    for the address (founder, 2026-10-07: 50 / 80 / 100). More than
    CUSTOMER_CANCEL_LOCK_HOURS before the slot is free. The charge is never
    collected on the spot — it is settled through the customer's wallet
    (MoneyService.on_booking_cancelled: paid − charge credited, or the
    charge debited), recorded in customer_charges. The customer may cancel
    until the captain heads out; they may EDIT a booking until
    CUSTOMER_EDIT_LOCK_MINUTES (60) before the slot; a plan wash cancelled
    within PLAN_WASH_FORFEIT_MINUTES (60) is used up (booking_service).
  - wallet_gating_enabled: whether a captain's wallet balance can block them
    from being assigned new bookings (see WalletService.is_eligible_for_
    assignment). Defaults OFF — there's no real payment gateway wired up
    yet (see KNOWN_ISSUES.md), so there's no legitimate way for a captain to
    actually top up their own balance; leaving this gate on with no way to
    clear it just blocks assignment outright. Flip it on once real top-ups
    exist.

Stored in the `settings` collection under key "booking_policy", same
pattern as PricingService's "pricing_config".
"""
import time

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException
from app.models.service_center import DEFAULT_WORKING_HOURS_END, DEFAULT_WORKING_HOURS_START
from app.repositories.content_repository import SettingRepository
from app.utils.money import round_rupees

DEFAULT_BOOKING_POLICY = {
    # LEGACY keys (see the module docstring) — kept equal to the real
    # opening hours, 7:00 AM – 7:00 PM (founder, 2026-10-07), so nothing
    # reading them disagrees with the website or the center defaults.
    "operating_start": DEFAULT_WORKING_HOURS_START,
    "operating_end": DEFAULT_WORKING_HOURS_END,
    "min_lead_minutes": 10,
    "slot_duration_minutes": 180,
    "slot_booking_cutoff_minutes": 30,
    # How far ahead a booking (or slot hold) is accepted, in days from today
    # IST inclusive — 7 means today + the next 6 days. Capacity planning is
    # done week-by-week; letting customers park bookings a month out just
    # creates no-shows and blocks slots nobody can plan staffing for.
    # Enforced uniformly (customer wizard, guest wizard, manager on-behalf
    # bookings, reschedules, slot holds); admins can raise it here.
    "max_advance_days": 7,
    "delay_tolerance_minutes": 20,
    "captain_travel_buffer_minutes": 15,
    "photo_geofence_radius_m": 300,
    # How long a captain can sit between "I've reached" (vehicle verified)
    # and the before-photo before the manager gets pinged — the
    # reached-but-not-started gap is the classic side-job window.
    "arrival_to_start_tolerance_minutes": 15,
    # How long past a slot's end a captain can still start before it's
    # considered "severely late" — mirrors the 30-minute pre-slot allowance
    # (START_WINDOW_MINUTES in booking_service.py) on the other side.
    "late_start_grace_minutes": 30,
    # Last-minute assignments: when a captain is handed a booking NEAR or
    # AFTER its scheduled start (customer books at 6:30 inside a 4-7 slot,
    # manager assigns 6:35), he cannot be "late" for a time that predates
    # him having the job — the booking was late, not the captain. His late
    # clock starts this many minutes AFTER assignment instead (default 15,
    # per the founder's rule: it's urgent, so 15 minutes to get moving).
    # See _effective_start_anchor in booking_service.py.
    "late_assignment_grace_minutes": 15,
    "captain_start_lockout_hours": 4,
    # How long a customer who chose "pay online" has to actually finish
    # paying before the unconfirmed booking is cancelled and its slot handed
    # back (default 30). Generous on purpose: a bank/UPI page can take a
    # while, and the customer can also switch the booking to cash instead.
    "payment_window_minutes": 30,
    # How many vehicles one customer can have wash on a single visit.
    # They share one slot seat: it's one trip to one address, so the travel
    # is paid and counted once (see create_booking_group).
    "max_vehicles_per_booking": 5,
    # A nudge this many minutes BEFORE the payment window closes, to a
    # customer who chose "pay online" and never finished (one per booking).
    "payment_reminder_minutes_before": 10,
    # "Time for a wash?" — sent to a customer this many days after their
    # last completed wash when nothing is booked and no pass is live, and
    # repeated at most once per the same number of days (default weekly).
    # Never to opted-out customers; only 10 AM–7 PM IST; WhatsApp only
    # through the approved marketing template. Admin-editable (min 3).
    "repeat_reminder_enabled": True,
    "repeat_reminder_days": 7,
    "wallet_gating_enabled": False,
    # Late-cancellation charges (₹, whole rupees) — the published policy
    # table, admin-editable (0..2000). See the module docstring.
    "cancellation_fee_1_to_4h": 50,
    "cancellation_fee_under_1h": 80,
    "cancellation_fee_after_captain_left": 100,
}

# The keys above that are money — validated 0..2000 on update.
CANCELLATION_FEE_KEYS = ("cancellation_fee_1_to_4h", "cancellation_fee_under_1h", "cancellation_fee_after_captain_left")
CANCELLATION_FEE_MAX = 2000


# Tiny process-local read cache. The policy is read on every slot request
# and 5+ times per reminder-loop pass but changes maybe once a month — a
# 10-second TTL removes almost all of that traffic while any admin edit
# (which busts the cache explicitly below) still shows up instantly on the
# single worker this app deliberately runs as.
_POLICY_CACHE: dict = {"value": None, "at": 0.0}
_POLICY_CACHE_TTL_SECONDS = 10.0


class BookingPolicyService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.settings_repo = SettingRepository(db)

    async def get_policy(self) -> dict:
        now = time.monotonic()
        if _POLICY_CACHE["value"] is not None and now - _POLICY_CACHE["at"] < _POLICY_CACHE_TTL_SECONDS:
            return dict(_POLICY_CACHE["value"])
        setting = await self.settings_repo.get_by_key("booking_policy")
        policy = dict(DEFAULT_BOOKING_POLICY)
        if setting:
            policy.update(setting.get("value", {}))
        _POLICY_CACHE["value"] = dict(policy)
        _POLICY_CACHE["at"] = now
        return policy

    async def set_policy(self, updates: dict, updated_by: str | None = None) -> dict:
        for key in CANCELLATION_FEE_KEYS:
            value = updates.get(key)
            if value is None:
                continue
            # Money on a customer's account: whole rupees, never negative,
            # never absurd — refused here too, not only by the request schema.
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= CANCELLATION_FEE_MAX:
                raise BadRequestException(f"{key} must be between ₹0 and ₹{CANCELLATION_FEE_MAX}.")
            updates[key] = round_rupees(value)
        current = await self.get_policy()
        new_length = updates.get("slot_duration_minutes")
        reshaped: list[str] = []
        if new_length is not None and int(new_length) != int(current.get("slot_duration_minutes") or 0):
            reshaped = await self._ensure_slot_length_change_is_safe(int(new_length))
        current.update({k: v for k, v in updates.items() if v is not None})
        await self.settings_repo.upsert("booking_policy", current, "Booking scheduling rules", updated_by=updated_by)
        _POLICY_CACHE["value"] = None  # bust — next read refetches
        if reshaped:
            from app.services.capacity_policy_service import CapacityPolicyService

            for center_id in reshaped:
                await CapacityPolicyService(self.settings_repo.db).rekey_for_current_slots(center_id)
        return current

    async def _ensure_slot_length_change_is_safe(self, new_length: int) -> list[str]:
        """The platform "Slot Length" shapes the slots of every center that
        has no length of its own. Changing it while such a center has live
        bookings, overrides or scheduled capacity changes on the current
        slots dropped their capacity limits (audit SLOT-01) — refused, naming
        the first center affected. Returns the centers whose slots WILL
        change (their capacity policy is re-split after the save)."""
        from app.services.booking_service import BookingService

        db = self.settings_repo.db
        bookings = BookingService(db)
        affected: list[str] = []
        async for center in db.service_centers.find(
            {"is_deleted": {"$ne": True}, "$or": [{"slot_duration_minutes": None}, {"slot_duration_minutes": 0}]}
        ):
            blockers = await bookings.slot_change_blockers(center, new_platform_slot_minutes=new_length)
            if blockers:
                raise BadRequestException(
                    f"Can't change the slot length yet — {center.get('name') or 'a service center'}: " + "; ".join(blockers) + "."
                )
            affected.append(str(center["_id"]))
        return affected
