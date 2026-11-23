"""
Franchise settlement (ARCHITECTURE.md §7.13 E) — ADMIN ONLY (managers,
captains and customers get 403). The statement per franchise center
(share accrued − cash the partner holds), and the settlements that post to
Finance. Every write is audited (module `franchise`).
"""
from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_db, require_admin
from app.core.responses import success
from app.schemas.franchise_schema import SettlementCreate, SettlementVoid
from app.services.franchise_service import FranchiseService

router = APIRouter(prefix="/franchise", tags=["Franchise"], dependencies=[Depends(require_admin)])


@router.get("/centers")
async def franchise_centers(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Every franchise center: partner, share %, accrued, settled, kits vs
    captains, fee status, net payable — plus the totals row."""
    return success(await FranchiseService(db).list_centers())


@router.get("/centers/{center_id}/statement")
async def franchise_statement(
    center_id: str,
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The month (when `month=YYYY-MM`) and all-time statement, settlements and ledger."""
    return success(await FranchiseService(db).statement(center_id, month))


@router.post("/centers/{center_id}/settlements")
async def post_settlement(
    center_id: str,
    payload: SettlementCreate,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    row = await FranchiseService(db).post_settlement(center_id, payload, current_user.id, current_user.role)
    return success(row, "Settlement recorded" if not row.get("replayed") else "Settlement already recorded")


@router.post("/centers/{center_id}/settlements/{settlement_id}/void")
async def void_settlement(
    center_id: str,
    settlement_id: str,
    payload: SettlementVoid,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await FranchiseService(db).void_settlement(center_id, settlement_id, payload.reason, current_user.id, current_user.role), "Settlement voided")
