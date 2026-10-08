"""
Captain wallet engine.

Rules (mirrors Rapido-style captain wallets, per platform requirements):
- The balance MAY go negative — on purpose, and only through job
  settlement: a captain who took cash from a customer holds the platform's
  money, and his wallet owes it back (a negative balance = money owed to the
  business, recovered from later earnings or handed in to the manager). The
  balance can NOT be taken negative by anything the captain asks for: a
  withdrawal is approved only while the balance covers it (allow_negative
  False, guarded in the same write), and admin corrections are explicit.
- A captain needs at least `minimum_balance` (default ₹50) in their wallet
  before a manager is allowed to assign them a new booking (when the
  admin's wallet_gating_enabled policy is on) — this caps the platform's
  cash-collection exposure.
- JOB SETTLEMENT (MoneyService.settle_captain_wallet, feature plan 1.5):
  per booking the wallet nets to  captain_earning (once the job is
  completed) − cash the captain took from the customer for it, posted as
  DELTAS against the booking's `captain_wallet_posted` (post_delta, keyed,
  inside a transaction). Completion credits his earning; a later doorstep
  cash collection debits the cash he now holds; an on-site add-on paid by
  QR changes nothing; nothing is ever posted twice.
- WITHDRAWAL: captain requests a payout of their wallet balance to their
  bank account. A request moves pending → approved (the amount is debited,
  once) → paid (the admin made the bank transfer; no money moves), or
  pending → rejected, or approved → rejected (payout cancelled: the amount
  is credited back). Nothing else (ADM-10: "approved" could never become
  "paid", and "paid" straight from pending skipped the approval step).
- TOPUP: captain (or admin, as a correction) adds money to reach the
  minimum balance.
"""
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import WalletTransactionType, WithdrawalStatus
from app.repositories.wallet_repository import CaptainWalletRepository, WalletTransactionRepository, WithdrawalRequestRepository
from app.utils.serializers import serialize_doc, serialize_list


# The withdrawal review state machine (ADM-10): current status → the
# statuses an admin may move it to. Each move is one guarded claim on the
# current status, so concurrent reviews take exactly one transition.
_WITHDRAWAL_MOVES: dict[str, set[str]] = {
    WithdrawalStatus.PENDING.value: {WithdrawalStatus.APPROVED.value, WithdrawalStatus.REJECTED.value},
    WithdrawalStatus.APPROVED.value: {WithdrawalStatus.PAID.value, WithdrawalStatus.REJECTED.value},
}


class WalletService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.wallet_repo = CaptainWalletRepository(db)
        self.txn_repo = WalletTransactionRepository(db)
        self.withdrawal_repo = WithdrawalRequestRepository(db)

    async def get_or_create_wallet(self, captain_id: str) -> dict:
        # Atomic upsert: the old find-then-create raced under concurrency
        # and could mint DUPLICATE wallets for one captain (caught by
        # tests/test_wallet_atomicity.py). One guarded upsert + the unique
        # captain_id index make that structurally impossible.
        from pymongo import ReturnDocument

        return await self.wallet_repo.collection.find_one_and_update(
            {"captain_id": captain_id},
            {"$setOnInsert": {"captain_id": captain_id, "balance": 0, "minimum_balance": 50.0}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

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


    async def _atomic_balance_change(
        self, captain_id: str, delta: float, txn_type: str, booking_id: str | None, description: str,
        allow_negative: bool = True, session=None, key: str | None = None,
    ) -> dict:
        """THE only way a balance ever changes: a single guarded
        find_one_and_update with $inc, so two concurrent operations can
        never overwrite each other (C1 in AUDIT.md — this is real money).
        When allow_negative is False the guard `balance >= -delta` is part
        of the filter itself: an insufficient balance makes the update
        match nothing instead of racing past the check."""
        from pymongo import ReturnDocument

        if session is None:
            await self.get_or_create_wallet(captain_id)
        query: dict = {"captain_id": captain_id}
        if delta < 0 and not allow_negative:
            query["balance"] = {"$gte": round(-delta, 2)}
        updated = await self.wallet_repo.collection.find_one_and_update(
            query,
            {"$inc": {"balance": round(delta, 2)},
             "$setOnInsert": {"captain_id": captain_id, "minimum_balance": 50.0}},
            # Inside a caller's transaction the wallet is created by this
            # same write (a separate upsert would run outside it).
            upsert=session is not None and (delta >= 0 or allow_negative),
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if updated is None:
            raise BadRequestException(
                "This action would take the captain's wallet negative. "
                "The captain must top up before taking more cash bookings."
            )
        new_balance = round(updated["balance"], 2)
        row = {
            "captain_id": captain_id,
            "wallet_id": str(updated["_id"]),
            "type": txn_type,
            "amount": round(abs(delta), 2),
            "balance_after": new_balance,
            "booking_id": booking_id,
            "description": description,
        }
        if key:
            # Unique (sparse) — a delta posted twice would collide here.
            row["key"] = key
        if session is None:
            await self.txn_repo.create(row)
        else:
            now = datetime.now(timezone.utc)
            await self.txn_repo.collection.insert_one(
                {**row, "created_at": now, "updated_at": now, "is_deleted": False}, session=session,
            )
        return serialize_doc(updated)

    async def post_delta(
        self, captain_id: str, delta: float, booking_id: str | None, description: str, *, session=None, key: str | None = None,
    ) -> dict:
        """A signed change (credit > 0, debit < 0) that may take the wallet
        negative — the captain-wallet settlement of a booking (see
        MoneyService.settle_captain_wallet)."""
        txn_type = WalletTransactionType.CREDIT.value if delta >= 0 else WalletTransactionType.DEBIT.value
        return await self._atomic_balance_change(
            captain_id, delta, txn_type, booking_id, description, allow_negative=True, session=session, key=key,
        )

    async def credit(self, captain_id: str, amount: float, booking_id: str | None, description: str) -> dict:
        return await self._atomic_balance_change(captain_id, amount, WalletTransactionType.CREDIT.value, booking_id, description)

    async def debit(self, captain_id: str, amount: float, booking_id: str | None, description: str, allow_negative: bool = False) -> dict:
        return await self._atomic_balance_change(
            captain_id, -amount, WalletTransactionType.DEBIT.value, booking_id, description, allow_negative=allow_negative
        )

    async def admin_adjust(self, captain_id: str, amount: float, description: str) -> dict:
        """Positive amount credits, negative amount debits. For manual
        corrections only — and only on an existing captain's wallet (ADM-09:
        a customer id or a garbage id used to mint a wallet and move money
        into it)."""
        captain = await self.db.users.find_one(
            {"_id": ObjectId(captain_id), "role": "captain", "is_deleted": {"$ne": True}}, {"_id": 1},
        ) if ObjectId.is_valid(captain_id) else None
        if not captain:
            raise NotFoundException("Captain not found")
        return await self._atomic_balance_change(captain_id, amount, WalletTransactionType.ADJUSTMENT.value, None, description)

    async def list_transactions(self, captain_id: str, page: int, page_size: int):
        items, total = await self.txn_repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total

    async def request_withdrawal(self, captain_id: str, amount: float) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        if amount > wallet["balance"]:
            raise BadRequestException("Withdrawal amount exceeds wallet balance")
        # One pending request at a time — stacking N requests each
        # individually ≤ balance let the SUM exceed it, and only the second
        # approval would discover that (after the first already paid out).
        pending = await self.withdrawal_repo.count({"captain_id": captain_id, "status": WithdrawalStatus.PENDING.value})
        if pending:
            raise BadRequestException("You already have a withdrawal request waiting for review.")
        try:
            created = await self.withdrawal_repo.create({"captain_id": captain_id, "amount": amount, "status": WithdrawalStatus.PENDING.value})
        except DuplicateKeyError:
            # Parallel requests all passed the count above; the partial
            # unique index (one pending row per captain) let one through.
            raise BadRequestException("You already have a withdrawal request waiting for review.")
        return serialize_doc(created)

    async def list_my_withdrawals(self, captain_id: str, page: int, page_size: int):
        items, total = await self.withdrawal_repo.list_for_captain(captain_id, page, page_size)
        return serialize_list(items), total

    async def list_pending_withdrawals(self, page: int, page_size: int, status: str = WithdrawalStatus.PENDING.value):
        """The admin's queues: "pending" (to approve / reject) and
        "approved" (debited, awaiting the bank transfer → mark paid)."""
        items, total = await self.withdrawal_repo.list_pending(page, page_size, status)
        # Who asked — one batched lookup for the page, never one per row.
        ids = list({i.get("captain_id") for i in items if i.get("captain_id") and ObjectId.is_valid(i["captain_id"])})
        names = {
            str(u["_id"]): u.get("full_name")
            for u in await self.db.users.find({"_id": {"$in": [ObjectId(x) for x in ids]}}, {"full_name": 1}).to_list(length=len(ids))
        } if ids else {}
        rows = serialize_list(items)
        for row in rows:
            row["captain_name"] = names.get(row.get("captain_id") or "")
        return rows, total

    async def review_withdrawal(self, withdrawal_id: str, status: str, reviewer_id: str, note: str | None) -> dict:
        withdrawal = await self.withdrawal_repo.find_by_id(withdrawal_id)
        if not withdrawal:
            raise NotFoundException("Withdrawal request not found")
        current = withdrawal.get("status") or WithdrawalStatus.PENDING.value
        if status not in _WITHDRAWAL_MOVES.get(current, set()):
            if current == WithdrawalStatus.PENDING.value and status == WithdrawalStatus.PAID.value:
                raise BadRequestException(
                    "Approve this request first — approving takes the amount from the captain's wallet; "
                    "mark it paid once the bank transfer is done."
                )
            raise BadRequestException(f"This withdrawal request has already been reviewed — it is {current}.")

        now = datetime.now(timezone.utc)
        if status == WithdrawalStatus.PAID.value:
            fields = {"status": status, "paid_by": reviewer_id, "paid_at": now, "payout_note": note}
        elif current == WithdrawalStatus.APPROVED.value:  # approved → rejected: payout cancelled
            fields = {"status": status, "rejected_by": reviewer_id, "rejected_at": now, "review_note": note, "returned_to_wallet": True}
        else:
            fields = {"status": status, "reviewed_by": reviewer_id, "reviewed_at": now, "review_note": note}

        # CLAIM the current state atomically BEFORE any money moves — a
        # double-click (or two admins) can't approve twice and debit 2×,
        # or pay out a request someone is cancelling. Exactly one caller
        # wins this guarded write; everyone else sees "already reviewed".
        claimed = await self.withdrawal_repo.update_if(withdrawal_id, {"status": withdrawal.get("status")}, fields)
        if claimed is None:
            raise BadRequestException("This withdrawal request has already been reviewed")

        async def _hand_back(exc: Exception):
            # Money didn't move — return the claim so the request can be
            # reviewed again instead of sitting in a state its money
            # doesn't match.
            await self.withdrawal_repo.update_if(
                withdrawal_id, {"status": status},
                {"status": current, **{k: withdrawal.get(k) for k in fields if k != "status"}},
            )
            raise exc

        if current == WithdrawalStatus.PENDING.value and status == WithdrawalStatus.APPROVED.value:
            try:
                await self.debit(withdrawal["captain_id"], withdrawal["amount"], None, "Wallet withdrawal to bank account", allow_negative=False)
            except BadRequestException as exc:
                # The debit's own wording is about cash bookings; this is
                # the balance having dropped since the request.
                await _hand_back(BadRequestException(
                    f"The captain's wallet no longer has ₹{withdrawal['amount']:g} — reject this request, "
                    "or ask them to request a smaller amount."
                ))
            except Exception as exc:  # noqa: BLE001
                await _hand_back(exc)
        elif current == WithdrawalStatus.APPROVED.value and status == WithdrawalStatus.REJECTED.value:
            try:
                await self.credit(withdrawal["captain_id"], withdrawal["amount"], None, "Withdrawal cancelled — amount returned to wallet")
            except Exception as exc:  # noqa: BLE001
                await _hand_back(exc)
        return serialize_doc(claimed)

    async def update_bank_details(self, captain_id: str, payload: dict) -> dict:
        wallet = await self.get_or_create_wallet(captain_id)
        updated = await self.wallet_repo.update_by_id(str(wallet["_id"]), payload)
        return serialize_doc(updated)
