from typing import Optional

from pydantic import BaseModel

from app.models.base import BusinessRecordBase

# The real opening hours (founder, 2026-10-07): 7:00 AM – 7:00 PM. The
# default for a new center and the fallback for one whose stored hours are
# missing — every slot generator reads these, never its own literal.
DEFAULT_WORKING_HOURS_START = "07:00"
DEFAULT_WORKING_HOURS_END = "19:00"


class ServiceCenterLocation(BaseModel):
    address: str
    city: str
    state: str
    pincode: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    service_pincodes: list[str] = []
    radius_km: float = 6.0


class ServiceCenterModel(BusinessRecordBase):
    name: str
    code: str
    location: ServiceCenterLocation
    manager_id: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    working_hours_start: str = DEFAULT_WORKING_HOURS_START
    working_hours_end: str = DEFAULT_WORKING_HOURS_END
    # Slot duration for THIS center — None falls back to the global
    # booking_policy default (see BookingPolicyService). Slots are always
    # generated from this center's own working_hours_start/end, never the
    # legacy global booking_policy.operating_start/end.
    slot_duration_minutes: Optional[int] = None
    # Optional hard cap on total bookings/day across all this center's
    # slots, independent of (and enforced alongside) each slot's own
    # capacity — None means no separate daily cap, only per-slot capacity
    # applies. See BookingService._reserve_slot_capacity.
    max_bookings_per_day: Optional[int] = None
    # Fallback capacity applied to a slot the FIRST time anyone reserves
    # into it (i.e. before an admin has explicitly set/overridden that
    # exact date+slot's own capacity via the slot_capacity collection).
    # Unconfigured (None, and no capacity policy) FAILS CLOSED: the slot
    # takes no bookings until capacity is set — see
    # BookingService._default_slot_capacity. It used to mean 999 seats.
    default_slot_capacity: Optional[int] = None
    is_active: bool = True
