"""Customer account charges (late-cancellation charges, founder 2026-10-07)
— see app/services/customer_charge_service.py.

  GET  /charges                     manager (own center) / admin — every charge and its history
  POST /charges/{charge_id}/adjust  manager (own center) / admin — reduce or waive (never raise)

Registered in main.py through booking_routes.charge_router."""
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Path, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_manager_or_admin,
)
from app.core.responses import paginated, success
from app.schemas.charge_schema import ChargeAdjustRequest
from app.services.customer_charge_service import CustomerChargeService
from app.utils.response_text import rupees

router = APIRouter(prefix="/charges", tags=["Customer Charges"])


@router.get("", dependencies=[Depends(require_manager_or_admin)])
async def list_charges(
    status: Optional[Literal["open", "applied", "waived", "settled"]] = None,
    customer_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Every late-cancellation charge — a manager's own center only, an
    admin's all of them — newest first, each with its full change history."""
    items, total = await CustomerChargeService(db).list_for_staff(
        actor_role=current_user.role, actor_center_id=current_user.service_center_id,
        status=status, customer_id=customer_id, page=pagination.page, page_size=pagination.page_size,
    )
    return paginated(items, pagination.page, pagination.page_size, total)


@router.post("/{charge_id}/adjust", dependencies=[Depends(require_manager_or_admin)])
async def adjust_charge(
    payload: ChargeAdjustRequest,
    charge_id: str = Path(pattern=r"^[0-9a-fA-F]{24}$"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Reduce a charge, or remove it (amount 0). Audit-logged with
    before/after inside the service; the customer is told."""
    service = CustomerChargeService(db)
    before = await service.collection.find_one({"_id": ObjectId(charge_id)}, {"amount": 1, "status": 1}) or {}
    result = await service.adjust(
        charge_id, payload.amount, payload.note,
        actor_id=current_user.id, actor_role=current_user.role, actor_center_id=current_user.service_center_id,
    )
    was = float(before.get("amount") or 0)
    now = float(result.get("amount") or 0)
    if abs(was - now) < 0.005 and before.get("status") == result.get("status"):
        return success(result, f"No change — the charge is already {rupees(now)}.")
    back = (
        f" {rupees(was - now)} was returned to the customer's wallet."
        if before.get("status") == "settled" and was - now >= 0.005 else ""
    )
    if result["status"] == "waived":
        return success(result, f"Charge of {rupees(was)} removed.{back}")
    return success(result, f"Charge reduced from {rupees(was)} to {rupees(now)}.{back}")
