from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.wallet_controller import WalletController
from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_admin,
    require_captain,
    require_manager_or_admin,
)
from app.schemas.wallet_schema import BankDetailsRequest, WalletAdjustmentRequest, WithdrawalCreateRequest, WithdrawalReviewRequest

router = APIRouter(prefix="/wallet", tags=["Captain Wallet"])


@router.get("/my", dependencies=[Depends(require_captain)])
async def my_wallet(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).my_wallet(current_user)


@router.get("/captain/{captain_id}", dependencies=[Depends(require_manager_or_admin)])
async def captain_wallet(captain_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).captain_wallet(current_user, captain_id)


# NOTE: the captain self-top-up endpoint was REMOVED deliberately. There is
# no payment gateway, so a "top-up" moved no real money — it let a captain
# credit their own wallet with any number and then request a genuine cash
# withdrawal against it. Cash handed to the manager is recorded through the
# admin adjustment endpoint below (an audited, staff-side action) instead.


@router.put("/bank-details", dependencies=[Depends(require_captain)])
async def update_bank_details(payload: BankDetailsRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).update_bank_details(current_user, payload)


@router.get("/my/transactions", dependencies=[Depends(require_captain)])
async def my_transactions(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).my_transactions(current_user, pagination)


@router.post("/withdrawals", dependencies=[Depends(require_captain)])
async def request_withdrawal(payload: WithdrawalCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).request_withdrawal(current_user, payload)


@router.get("/withdrawals/my", dependencies=[Depends(require_captain)])
async def my_withdrawals(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).my_withdrawals(current_user, pagination)


@router.get("/withdrawals/pending", dependencies=[Depends(require_admin)])
async def pending_withdrawals(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).pending_withdrawals(pagination)


@router.put("/withdrawals/{withdrawal_id}/review", dependencies=[Depends(require_admin)])
async def review_withdrawal(withdrawal_id: str, payload: WithdrawalReviewRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).review_withdrawal(current_user, withdrawal_id, payload)


@router.post("/captain/{captain_id}/adjust", dependencies=[Depends(require_admin)])
async def admin_adjust(captain_id: str, payload: WalletAdjustmentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await WalletController(db).admin_adjust(current_user, captain_id, payload)
