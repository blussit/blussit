"""
Captain wallet engine.

Rules (mirrors Rapido-style captain wallets, per platform requirements):
- A captain's wallet balance must never go negative.
- A captain needs at least `minimum_balance` (default ₹50) in their wallet
  before a manager is allowed to assign them a new booking — this protects
  the platform's cash-collection exposure (see below).
- CREDIT: happens when a booking is paid ONLINE. The platform collects the
  full amount, so it pushes the captain's earned share (petrol + service
  fee, see PricingService) into their wallet.
- DEBIT: happens when a booking is paid CASH. The captain collects the full
  amount directly from the customer, so the platform's share is deducted
  from the captain's wallet balance (the captain already has that money in
  hand physically; the wallet simply reconciles what they owe the
  platform). This is why the minimum balance matters — a captain with too
  little balance couldn't safely take on a cash booking without risking
  going negative.
- WITHDRAWAL: captain requests a payout of their wallet balance to their
  bank account. Debited immediately on approval (kept simple; a real bank
  payout integration would debit on successful transfer instead).
- TOPUP: captain (or admin, as a correction) adds money to reach the
  minimum balance.
"""
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import WalletTransactionType, WithdrawalStatus
from app.repositories.wallet_repository import CaptainWalletRepository, WalletTransactionRepository, WithdrawalRequestRepository
from app.utils.serializers import serialize_doc, serialize_list


class WalletService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.wallet_repo = CaptainWalletRepository(db)
        self.txn_repo = WalletTransactionRepository(db)
        self.withdrawal_repo = WithdrawalRequestRepository(db)

    async def get_or_create_wallet(self, captain_id: str) -> dict:
        wallet = await self.wallet_repo.find_by_captain(captain_id)
        if wallet:
            return wallet
        return await self.wallet_repo.create({"captain_id": captain_id, "balance": 0, "minimum_balance": 50.0})

    async def get_wallet_view(self, captain_id: str) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        return serialize_doc(wallet)

    async def is_eligible_for_assignment(self, captain_id: str, policy: dict | None = None) -> bool:
        # Admin-toggleable — see BookingPolicyService's wallet_gating_enabled
        # doc comment. Off by default since there's no real top-up path yet.
        if policy is not None and not policy.get("wallet_gating_enabled", False):
            return True
        wallet = await self.get_or_create_wallet(captain_id)
        return wallet["balance"] >= wallet.get("minimum_balance", 50.0)

    async def credit(self, captain_id: str, amount: float, booking_id: str | None, description: str) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        new_balance = round(wallet["balance"] + amount, 2)
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), {"balance": new_balance})
        await self.txn_repo.create(
            {
                "captain_id": captain_id,
                "wallet_id": str(wallet["_id"]),
                "type": WalletTransactionType.CREDIT.value,
                "amount": amount,
                "balance_after": new_balance,
                "booking_id": booking_id,
                "description": description,
            }
        )
        return serialize_doc(updated)

    async def debit(self, captain_id: str, amount: float, booking_id: str | None, description: str, allow_negative: bool = False) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        new_balance = round(wallet["balance"] - amount, 2)
        if new_balance < 0 and not allow_negative:
            raise BadRequestException(
                f"This action would take the captain's wallet negative (₹{new_balance}). "
                "The captain must top up before taking more cash bookings."
            )
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), {"balance": new_balance})
        await self.txn_repo.create(
            {
                "captain_id": captain_id,
                "wallet_id": str(wallet["_id"]),
                "type": WalletTransactionType.DEBIT.value,
                "amount": amount,
                "balance_after": new_balance,
                "booking_id": booking_id,
                "description": description,
            }
        )
        return serialize_doc(updated)

    async def top_up(self, captain_id: str, amount: float) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        new_balance = round(wallet["balance"] + amount, 2)
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), {"balance": new_balance})
        await self.txn_repo.create(
            {
                "captain_id": captain_id,
                "wallet_id": str(wallet["_id"]),
                "type": WalletTransactionType.TOPUP.value,
                "amount": amount,
                "balance_after": new_balance,
                "booking_id": None,
                "description": "Captain top-up",
            }
        )
        return serialize_doc(updated)

    async def admin_adjust(self, captain_id: str, amount: float, description: str) -> dict:
        """Positive amount credits, negative amount debits. For manual corrections only."""
        wallet = await self.get_or_create_wallet(captain_id)
        new_balance = round(wallet["balance"] + amount, 2)
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), {"balance": new_balance})
        await self.txn_repo.create(
            {
                "captain_id": captain_id,
                "wallet_id": str(wallet["_id"]),
                "type": WalletTransactionType.ADJUSTMENT.value,
                "amount": amount,
                "balance_after": new_balance,
                "booking_id": None,
                "description": description,
            }
        )
        return serialize_doc(updated)

    async def list_transactions(self, captain_id: str, page: int, page_size: int):
        items, total = await self.txn_repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total

    async def request_withdrawal(self, captain_id: str, amount: float) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        if amount > wallet["balance"]:
            raise BadRequestException("Withdrawal amount exceeds wallet balance")
        created = await self.withdrawal_repo.create({"captain_id": captain_id, "amount": amount, "status": WithdrawalStatus.PENDING.value})
        return serialize_doc(created)

    async def list_my_withdrawals(self, captain_id: str, page: int, page_size: int):
        items, total = await self.withdrawal_repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total

    async def list_pending_withdrawals(self, page: int, page_size: int):
        items, total = await self.withdrawal_repo.list_pending(page, page_size)
        return serialize_list(items), total

    async def review_withdrawal(self, withdrawal_id: str, status: str, reviewer_id: str, note: str | None) -> dict:
        withdrawal = await self.withdrawal_repo.find_by_id(withdrawal_id)
        if not withdrawal:
            raise NotFoundException("Withdrawal request not found")
        if withdrawal["status"] != WithdrawalStatus.PENDING.value:
            raise BadRequestException("This withdrawal request has already been reviewed")

        if status == WithdrawalStatus.APPROVED.value or status == WithdrawalStatus.PAID.value:
            await self.debit(withdrawal["captain_id"], withdrawal["amount"], None, "Wallet withdrawal to bank account", allow_negative=False)

        updated = await self.withdrawal_repo.update_by_id(withdrawal_id, {"status": status, "reviewed_by": reviewer_id, "review_note": note})
        return serialize_doc(updated)

    async def update_bank_details(self, captain_id: str, payload: dict) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), payload)
        return serialize_doc(updated)
