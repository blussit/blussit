from app.repositories.base_repository import BaseRepository


class SlotCapacityRepository(BaseRepository):
    """One document per (service_center_id, date, slot_key) — see
    BookingService._reserve_slot_capacity for the atomic reservation/release
    logic built on top of BaseRepository.get_or_init/increment_if."""
    collection_name = "slot_capacity"


class DailyCapacityRepository(BaseRepository):
    """One document per (service_center_id, date) — the optional
    center-wide daily cap, layered on top of (not a replacement for)
    per-slot capacity. Only consulted when the center has
    max_bookings_per_day configured."""
    collection_name = "daily_capacity"
