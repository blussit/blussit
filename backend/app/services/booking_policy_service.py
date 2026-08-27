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
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.content_repository import SettingRepository

DEFAULT_BOOKING_POLICY = {
    "operating_start": "09:00",
    "operating_end": "19:00",
    "min_lead_minutes": 10,
    "slot_duration_minutes": 180,
    "slot_booking_cutoff_minutes": 30,
    "delay_tolerance_minutes": 20,
    "captain_travel_buffer_minutes": 15,
    "photo_geofence_radius_m": 300,
    # How long past a slot's end a captain can still start before it's
    # considered "severely late" — mirrors the 30-minute pre-slot allowance
    # (START_WINDOW_MINUTES in booking_service.py) on the other side.
    "late_start_grace_minutes": 30,
    "captain_start_lockout_hours": 4,
    "wallet_gating_enabled": False,
}


class BookingPolicyService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.settings_repo = SettingRepository(db)

    async def get_policy(self) -> dict:
        setting = await self.settings_repo.get_by_key("booking_policy")
        policy = dict(DEFAULT_BOOKING_POLICY)
        if setting:
            policy.update(setting.get("value", {}))
        return policy

    async def set_policy(self, updates: dict) -> dict:
        current = await self.get_policy()
        current.update({k: v for k, v in updates.items() if v is not None})
        await self.settings_repo.upsert("booking_policy", current, "Booking scheduling rules")
        return current
