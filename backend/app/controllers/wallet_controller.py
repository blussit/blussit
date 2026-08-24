from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.wallet_schema import BankDetailsRequest, TopUpRequest, WalletAdjustmentRequest, WithdrawalCreateRequest, WithdrawalReviewRequest
from app.services.audit_service import AuditService
from app.services.wallet_service import WalletService


class WalletController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = WalletService(db)
        self.audit = AuditService(db)

    async def my_wallet(self, current_user: CurrentUser):
        return success(await self.service.get_wallet_view(current_user.id))

    async def captain_wallet(self, captain_id: str):
        return success(await self.service.get_wallet_view(captain_id))

    async def top_up(self, current_user: CurrentUser, payload: TopUpRequest):
        result = await self.service.top_up(current_user.id, payload.amount)
        return success(result, "Wallet topped up successfully")

    async def update_bank_details(self, current_user: CurrentUser, payload: BankDetailsRequest):
        result = await self.service.update_bank_details(current_user.id, payload.model_dump())
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

    async def pending_withdrawals(self, pagination: PaginationParams):
        items, total = await self.service.list_pending_withdrawals(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def review_withdrawal(self, current_user: CurrentUser, withdrawal_id: str, payload: WithdrawalReviewRequest):
        result = await self.service.review_withdrawal(withdrawal_id, payload.status, current_user.id, payload.review_note)
        await self.audit.log_action(current_user.id, current_user.role, "REVIEW_WITHDRAWAL", "withdrawals", withdrawal_id, {"status": payload.status})
        return success(result, "Withdrawal request reviewed")

    async def admin_adjust(self, current_user: CurrentUser, captain_id: str, payload: WalletAdjustmentRequest):
        result = await self.service.admin_adjust(captain_id, payload.amount, payload.description)
        await self.audit.log_action(current_user.id, current_user.role, "ADJUST_WALLET", "captain_wallets", captain_id, {"amount": payload.amount, "description": payload.description})
        return success(result, "Wallet adjusted successfully")
