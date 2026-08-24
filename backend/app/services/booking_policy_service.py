"""
Booking policy — the admin-configurable rules that keep scheduling honest:

  - operating_start / operating_end: the daily window customers can book
    within (default 09:00-19:00 IST).
  - min_lead_minutes: how far in advance a booking must be made (default 10).
  - slot_granularity_minutes: customers pick a time snapped to this
    granularity (default 60, i.e. on the hour).
  - captain_travel_buffer_minutes: minutes blocked out before AND after a
    captain's booking window, during which they cannot be assigned another
    job — models real travel time between jobs (default 15).
  - photo_geofence_radius_m: how far a before/after photo's GPS can be from
    the booking address before it gets flagged for manager review, rather
    than blocked outright (default 300m — service addresses aren't
    pinpoint-accurate, so this stays generous).
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
    "slot_granularity_minutes": 60,
    "captain_travel_buffer_minutes": 15,
    "photo_geofence_radius_m": 300,
    # How long past a slot's end a captain can still start before it's
    # considered "severely late" — mirrors the 30-minute pre-slot allowance
    # (START_WINDOW_MINUTES in booking_service.py) on the other side.
    "late_start_grace_minutes": 30,
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
