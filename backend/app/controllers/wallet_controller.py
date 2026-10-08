from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams
from app.core.exceptions import NotFoundException
from app.core.responses import paginated, success
from app.repositories.user_repository import UserRepository
from app.schemas.wallet_schema import BankDetailsRequest, WalletAdjustmentRequest, WithdrawalCreateRequest, WithdrawalReviewRequest
from app.services.audit_service import AuditService
from app.services.wallet_service import WalletService


class WalletController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.service = WalletService(db)
        self.audit = AuditService(db)

    async def my_wallet(self, current_user: CurrentUser):
        return success(await self.service.get_wallet_view(current_user.id))

    async def captain_wallet(self, current_user: CurrentUser, captain_id: str):
        # Center-scoped, and bank details are masked for managers — the
        # account number's only legitimate reader is the admin paying out.
        captain = await UserRepository(self.db).find_by_id(captain_id)
        if not captain or captain.get("role") != "captain":
            raise NotFoundException("Captain not found")
        ensure_own_center(current_user.role, current_user.service_center_id, captain.get("service_center_id"))
        view = await self.service.get_wallet_view(captain_id)
        if current_user.role != "admin" and view.get("bank_account_number"):
            acct = str(view["bank_account_number"])
            view["bank_account_number"] = "•" * max(0, len(acct) - 4) + acct[-4:]
        return success(view)

    async def update_bank_details(self, current_user: CurrentUser, payload: BankDetailsRequest):
        result = await self.service.update_bank_details(current_user.id, payload.model_dump())
        # Where payouts go is the classic target of an account takeover —
        # every change leaves a trail (last 4 digits only, never the number).
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_BANK_DETAILS", "captain_wallets", result.get("id"),
            {"account_last4": payload.bank_account_number[-4:], "ifsc": payload.bank_ifsc},
        )
        return success(result, "Bank details saved")

    async def my_transactions(self, current_user: CurrentUser, pagination: PaginationParams):
        items, total = await self.service.list_transactions(current_user.id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def request_withdrawal(self, current_user: CurrentUser, payload: WithdrawalCreateRequest):
        result = await self.service.request_withdrawal(current_user.id, payload.amount)
        return success(result, "Withdrawal request submitted")

    async def my_withdrawals(self, current_user: CurrentUser, pagination: PaginationParams):
        items, total = await self.service.list_my_withdrawals(current_user.id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def pending_withdrawals(self, pagination: PaginationParams, status: str = "pending"):
        items, total = await self.service.list_pending_withdrawals(pagination.page, pagination.page_size, status)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def review_withdrawal(self, current_user: CurrentUser, withdrawal_id: str, payload: WithdrawalReviewRequest):
        before = await self.service.withdrawal_repo.find_by_id(withdrawal_id)
        result = await self.service.review_withdrawal(withdrawal_id, payload.status, current_user.id, payload.review_note)
        await self.audit.log_action(
            current_user.id, current_user.role, "REVIEW_WITHDRAWAL", "withdrawals", withdrawal_id,
            {
                "status": payload.status, "amount": result.get("amount"), "captain_id": result.get("captain_id"),
                "changes": {"status": {"before": (before or {}).get("status"), "after": result.get("status")}},
            },
        )
        return success(result, "Withdrawal marked paid" if payload.status == "paid" else "Withdrawal request reviewed")

    async def admin_adjust(self, current_user: CurrentUser, captain_id: str, payload: WalletAdjustmentRequest):
        # 404 for anything but an existing captain (ADM-09) — checked in the
        # service before any wallet is read or created.
        result = await self.service.admin_adjust(captain_id, payload.amount, payload.description)
        after = float(result.get("balance") or 0)
        await self.audit.log_action(
            current_user.id, current_user.role, "ADJUST_WALLET", "captain_wallets", captain_id,
            {
                "amount": payload.amount, "description": payload.description,
                "balance_before": round(after - payload.amount, 2), "balance_after": after,
            },
        )
        return success(result, "Wallet adjusted successfully")
