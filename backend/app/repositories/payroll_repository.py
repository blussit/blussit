"""Payroll ledger — append-only rows of integer paise per captain.
Balances are always the SUM of rows (never a stored counter). `key` is unique:
a client idempotency key or a deterministic system key (`cm:{booking}:{rev}`,
`sal:{captain}:{YYYY-MM}`), so a retry or a double submit lands once."""
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.base_repository import BaseRepository

# Rows that move what the captain is OWED (dues = their signed sum). Earnings
# are positive; a payout / advance is stored negative; a void is positive again.
DUE_KINDS = (
    "commission", "commission_adjust", "salary_accrual", "allowance", "bonus", "deduction", "advance", "payout", "payout_void",
)
EARN_KINDS = ("commission", "commission_adjust", "salary_accrual", "allowance", "bonus", "deduction")
PAID_KINDS = ("advance", "payout", "payout_void")
DEPOSIT_KINDS = ("deposit_received", "deposit_refund", "deposit_forfeit")


class PayrollRepository(BaseRepository):
    collection_name = "payroll_ledger"

    async def sums_by_kind(self, captain_id: str, session=None, extra: dict | None = None) -> dict[str, int]:
        match = {"captain_id": captain_id, **(extra or {})}
        rows = await self.collection.aggregate(
            [{"$match": match}, {"$group": {"_id": "$kind", "paise": {"$sum": "$amount_paise"}}}], session=session,
        ).to_list(length=40)
        return {r["_id"]: int(r["paise"] or 0) for r in rows}

    async def dues(self, captain_id: str, session=None) -> dict:
        """{earned, paid, dues, deposit_held} in paise. `paid` is positive
        money handed over (net of voids)."""
        sums = await self.sums_by_kind(captain_id, session=session)
        earned = sum(sums.get(k, 0) for k in EARN_KINDS)
        paid = -sum(sums.get(k, 0) for k in PAID_KINDS)
        deposit = sums.get("deposit_received", 0) + sums.get("deposit_refund", 0) + sums.get("deposit_forfeit", 0)
        return {"earned_paise": earned, "paid_paise": paid, "dues_paise": earned - paid, "deposit_held_paise": deposit}


async def ensure_payroll_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db.payroll_ledger
    await c.create_index("key", unique=True, name="uniq_payroll_key")
    await c.create_index([("captain_id", 1), ("created_at", -1)], name="captain_created")
    await c.create_index([("service_center_id", 1), ("created_at", -1)], name="center_created")
    await c.create_index([("captain_id", 1), ("kind", 1), ("period", 1)], name="captain_kind_period")
    await c.create_index([("booking_id", 1), ("kind", 1)], name="booking_kind", sparse=True)
    await c.create_index("voids", name="voids", sparse=True)
    # Commission accrual reads: a captain's commissions by completion time.
    await db.bookings.create_index(
        [("commission_captain_id", 1), ("completed_at", -1)], name="commission_captain_completed",
        partialFilterExpression={"commission_captain_id": {"$type": "string"}},
    )
    await db.attendance.create_index([("service_center_id", 1), ("attendance_date", 1)], name="center_date", sparse=True)
