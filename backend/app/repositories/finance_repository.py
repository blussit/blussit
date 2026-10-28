"""Finance ledger — the admin's own credit/debit entries (expenses, product
sales, salary, marketing …). Separate from bookings. See FinanceService."""
from app.repositories.base_repository import BaseRepository


class FinanceRepository(BaseRepository):
    collection_name = "finance_entries"


async def ensure_finance_indexes(db) -> None:
    """List (newest first), per-type sums and per-category sums are all
    bounded by entry_date, so every index leads with the filter and ends in
    the date."""
    c = db.finance_entries
    await c.create_index([("is_deleted", 1), ("entry_date", -1)], name="deleted_date")
    await c.create_index([("type", 1), ("entry_date", 1)], name="type_date")
    await c.create_index([("category", 1), ("entry_date", 1)], name="category_date")
