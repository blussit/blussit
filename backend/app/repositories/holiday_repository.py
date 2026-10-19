"""Holidays — admin-declared breaks when nobody can book washes (all
centers, or a chosen few). See HolidayService for the rules."""
from app.repositories.base_repository import BaseRepository


class HolidayRepository(BaseRepository):
    collection_name = "holidays"


async def ensure_holiday_indexes(db) -> None:
    """(active, end_at): the in-process cache loads every live holiday with
    one indexed read; (service_center_ids, end_at): a center's holidays."""
    await db.holidays.create_index([("active", 1), ("end_at", 1)], name="active_end_at")
    await db.holidays.create_index([("service_center_ids", 1), ("end_at", 1)], name="centers_end_at")
