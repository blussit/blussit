"""
Finance ledger — ADMIN ONLY (managers, captains and customers get 403).
Expenses, product sales and other credit/debit entries; separate from
bookings. See FinanceService. Every write is audited (FINANCE_ENTRY_CREATE /
UPDATE / VOID, FINANCE_EXPORT).
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_db, require_admin
from app.core.responses import paginated, success
from app.schemas.finance_schema import FinanceEntryCreate, FinanceEntryUpdate, FinanceVoidRequest
from app.services.finance_service import FinanceService

router = APIRouter(prefix="/admin/finance", tags=["Finance"], dependencies=[Depends(require_admin)])

_DAY = r"^\d{4}-\d{2}-\d{2}$"


def _filters(
    type: Literal["credit", "debit"] | None = Query(None),
    category: str | None = Query(None, max_length=50),
    date_from: str | None = Query(None, alias="from", pattern=_DAY),
    date_to: str | None = Query(None, alias="to", pattern=_DAY),
    q: str | None = Query(None, max_length=100),
    done_by: str | None = Query(None, max_length=100),
    include_voided: bool = Query(False),
) -> dict:
    return {"type": type, "category": category, "date_from": date_from, "date_to": date_to, "q": q, "done_by": done_by, "include_voided": include_voided}


@router.get("/entries")
async def list_entries(
    filters: dict = Depends(_filters),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    items, total = await FinanceService(db).list(page, page_size, **filters)
    return paginated(items, page, page_size, total)


@router.get("/entries.csv")
async def export_entries(filters: dict = Depends(_filters), current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    body = await FinanceService(db).export_csv(current_user.id, current_user.role, **filters)
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="blussit-finance.csv"', "Cache-Control": "no-store"},
    )


@router.post("/entries")
async def create_entry(payload: FinanceEntryCreate, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FinanceService(db).create(payload, current_user.id, current_user.role), "Entry added")


@router.patch("/entries/{entry_id}")
async def update_entry(entry_id: str, payload: FinanceEntryUpdate, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FinanceService(db).update(entry_id, payload, current_user.id, current_user.role), "Entry updated")


@router.post("/entries/{entry_id}/void")
async def void_entry(entry_id: str, payload: FinanceVoidRequest, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FinanceService(db).void(entry_id, payload.reason, current_user.id, current_user.role), "Entry voided")


@router.get("/summary")
async def summary(
    date_from: str | None = Query(None, alias="from", pattern=_DAY),
    date_to: str | None = Query(None, alias="to", pattern=_DAY),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await FinanceService(db).summary(date_from, date_to))


@router.get("/categories")
async def categories(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FinanceService(db).categories())
