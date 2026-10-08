"""Customer wallet (founder 2026-10-07, spec §1.1) — see
app/services/customer_wallet_service.py.

  GET  /wallet/me                           customer — balance + ledger (paginated)
  GET  /customers/{customer_id}/wallet      manager (customer known to their center) / admin;
                                            entries carry own_center (a manager sees
                                            "Other Center" for another center's booking)
  POST /customers/{customer_id}/wallet/payout   manager (booking's center) / admin — record a
                                            PAYBACK for one booking that was cancelled,
                                            delayed or had an issue (MONEY-2)
  POST /customers/{customer_id}/wallet/adjust   admin — free correction (audited)
  GET  /wallet/payouts                      admin — every payback, newest first
                                            (?service_center_id=&date_from=&date_to=)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_admin,
    require_customer,
    require_manager_or_admin,
)
from app.core.responses import paginated, success
from app.schemas.customer_wallet_schema import WalletAdjustRequest, WalletPayoutRequest
from app.services.customer_wallet_service import CustomerWalletService

router = APIRouter(tags=["Customer Wallet"])

_CUSTOMER_ID = Path(pattern=r"^[0-9a-fA-F]{24}$")


@router.get("/wallet/me", dependencies=[Depends(require_customer)])
async def my_wallet(
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The signed-in customer's balance (credit_available, and
    previous_balance_due — what the next booking will carry) and ledger."""
    service = CustomerWalletService(db)
    items, total = await service.ledger(current_user.id, pagination.page, pagination.page_size)
    return success({**await service.summary(current_user.id), "items": items, "total": total,
                    "page": pagination.page, "page_size": pagination.page_size})


_DAY = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/wallet/payouts", dependencies=[Depends(require_admin)])
async def list_payouts(
    pagination: PaginationParams = Depends(),
    service_center_id: Optional[str] = Query(default=None, pattern=r"^[0-9a-fA-F]{24}$"),
    date_from: Optional[str] = Query(default=None, pattern=_DAY),
    date_to: Optional[str] = Query(default=None, pattern=_DAY),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Every payback: manager paybacks (wallet and goodwill — booking
    number, reason, method, who paid, center) and older wallet payouts,
    newest first; by center and IST day range (paid date)."""
    items, total = await CustomerWalletService(db).payouts(
        pagination.page, pagination.page_size, service_center_id, date_from, date_to,
    )
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/customers/{customer_id}/wallet", dependencies=[Depends(require_manager_or_admin)])
async def customer_wallet(
    customer_id: str = _CUSTOMER_ID,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """A customer's wallet for staff — a manager only for a customer known
    to their center (404 otherwise)."""
    return success(await CustomerWalletService(db).staff_view(
        customer_id, current_user.role, current_user.service_center_id, pagination.page, pagination.page_size,
    ))


@router.post("/customers/{customer_id}/wallet/payout", dependencies=[Depends(require_manager_or_admin)])
async def record_payout(
    payload: WalletPayoutRequest,
    customer_id: str = _CUSTOMER_ID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """MONEY-2: a payback for ONE booking of this customer that was
    cancelled, delayed or had an issue — paid by the manager in cash or
    online and recorded here. Wallet credit is debited first; the rest is a
    goodwill payback on the booking. Per booking ≤ what the customer paid.
    400 PAYBACK_NOT_ALLOWED / PAYBACK_TOO_MUCH / WALLET_BALANCE_TOO_LOW;
    404 for a booking outside the manager's center or not this customer's.
    Idempotent (same reference / idempotency_key → created: false)."""
    record = await CustomerWalletService(db).payback(
        customer_id, booking_id=payload.booking_id, amount=payload.amount, reason=payload.reason, method=payload.method,
        reference=payload.reference, note=payload.note, idempotency_key=payload.idempotency_key,
        actor={"id": current_user.id, "role": current_user.role}, center=current_user.service_center_id,
    )
    return success(record, "Payback recorded" if record.get("created") else "This payback was already recorded")


@router.post("/customers/{customer_id}/wallet/adjust", dependencies=[Depends(require_admin)])
async def adjust_wallet(
    payload: WalletAdjustRequest,
    customer_id: str = _CUSTOMER_ID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    entry = await CustomerWalletService(db).adjust(
        customer_id, payload.amount, payload.note, {"id": current_user.id, "role": current_user.role},
        idempotency_key=payload.idempotency_key,
    )
    return success(entry, "Wallet adjusted" if entry.get("created") else "This adjustment was already applied")
