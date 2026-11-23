"""
Captain pay (ARCHITECTURE.md §7.13). Managers reach their OWN center's
captains only (a cross-center id is 403), admins any; a captain reaches only
`/payroll/me`; customers get 403. Every amount is integer paise, every write is
audited, and money writes take a client `idempotency_key` so a double tap lands
once.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_db, require_captain, require_manager_or_admin
from app.core.responses import paginated, success
from app.schemas.payroll_schema import (
    AdjustmentCreate, AttendanceOverride, CommissionSet, DepositAction, FinalizeRequest, KitReturn, KitUpdate, PaymentCreate,
    PayTermsUpdate, ReasonRequest,
)
from app.services.payroll_service import PayrollService

router = APIRouter(prefix="/payroll", tags=["Payroll"])

_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


def _svc(db: AsyncIOMotorDatabase) -> PayrollService:
    return PayrollService(db)


# ---------------------------------------------------------------- captain's own view


@router.get("/me")
async def my_pay(
    month: str | None = Query(None, pattern=_MONTH), current_user: CurrentUser = Depends(require_captain), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).me(current_user, month))


@router.get("/me/statement")
async def my_statement(
    month: str | None = Query(None, pattern=_MONTH), current_user: CurrentUser = Depends(require_captain), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).me_statement(current_user, month))


# ---------------------------------------------------------------- the Salary page


@router.get("/captains")
async def list_captains(
    center_id: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=20),
    basis: str | None = Query(None, pattern=r"^(salary|commission|legacy)$"),
    month: str | None = Query(None, pattern=_MONTH),
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).list_captains(current_user, center_id, status, basis, month))


@router.get("/summary")
async def payroll_summary(
    center_id: str | None = Query(None, max_length=40),
    month: str | None = Query(None, pattern=_MONTH),
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).summary(current_user, center_id, month))


@router.get("/export.csv")
async def export_csv(
    center_id: str | None = Query(None, max_length=40),
    status: str | None = Query(None, max_length=20),
    basis: str | None = Query(None, pattern=r"^(salary|commission|legacy)$"),
    month: str | None = Query(None, pattern=_MONTH),
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    body = await _svc(db).export_csv(current_user, center_id, status, basis, month)
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="blussit-captain-pay.csv"', "Cache-Control": "no-store"},
    )


@router.get("/captains/{captain_id}")
async def captain_detail(captain_id: str, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await _svc(db).captain_detail(captain_id, current_user))


@router.put("/captains/{captain_id}/terms")
async def update_terms(
    captain_id: str, payload: PayTermsUpdate, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).update_terms(captain_id, payload, current_user), "Pay terms saved")


@router.get("/captains/{captain_id}/statement")
async def captain_statement(
    captain_id: str, month: str | None = Query(None, pattern=_MONTH),
    current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).captain_statement(captain_id, month, current_user))


@router.get("/captains/{captain_id}/ledger")
async def captain_ledger(
    captain_id: str,
    kind: str | None = Query(None, max_length=30),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(30, ge=1, le=100),
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    items, total = await _svc(db).ledger_page(captain_id, current_user, page, page_size, kind)
    return paginated(items, page, page_size, total)


@router.get("/captains/{captain_id}/commissions")
async def captain_commissions(
    captain_id: str, month: str | None = Query(None, pattern=_MONTH),
    current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).captain_commissions(captain_id, month, current_user))


# ---------------------------------------------------------------- money in / out


@router.post("/captains/{captain_id}/payments")
async def record_payment(
    captain_id: str, payload: PaymentCreate, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await _svc(db).record_payment(captain_id, payload, current_user)
    return success(result, "Payment already recorded" if result.get("duplicate") else "Payment recorded")


@router.post("/payments/{ledger_id}/void")
async def void_payment(
    ledger_id: str, payload: ReasonRequest, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await _svc(db).void_payment(ledger_id, payload.reason, current_user)
    return success(result, "Payment already voided" if result.get("duplicate") else "Payment voided")


@router.post("/captains/{captain_id}/adjustments")
async def record_adjustment(
    captain_id: str, payload: AdjustmentCreate, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await _svc(db).record_adjustment(captain_id, payload, current_user)
    return success(result, "Adjustment already recorded" if result.get("duplicate") else "Adjustment recorded")


@router.post("/captains/{captain_id}/attendance")
async def mark_attendance(
    captain_id: str, payload: AttendanceOverride, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).mark_attendance(captain_id, payload, current_user), "Attendance saved")


@router.put("/bookings/{booking_id}/commission")
async def set_booking_commission(
    booking_id: str, payload: CommissionSet, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(
        await _svc(db).set_booking_commission(booking_id, payload.amount_paise, payload.percent, payload.reason, current_user), "Commission saved",
    )


@router.post("/captains/{captain_id}/months/{month}/finalize")
async def finalize_month(
    captain_id: str, month: str, payload: FinalizeRequest | None = None,
    current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await _svc(db).finalize_month(captain_id, month, payload or FinalizeRequest(), current_user)
    return success(result, "Month finalized" if result.get("posted") else "Nothing changed — this month is already up to date")


@router.post("/captains/{captain_id}/deposit")
async def deposit_action(
    captain_id: str, payload: DepositAction, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).deposit_action(captain_id, payload, current_user), "Deposit saved")


@router.put("/captains/{captain_id}/kit")
async def update_kit(
    captain_id: str, payload: KitUpdate, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).update_kit(captain_id, payload, current_user), "Kit saved")


@router.post("/captains/{captain_id}/kit/return")
async def return_kit(
    captain_id: str, payload: KitReturn | None = None, current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await _svc(db).return_kit(captain_id, payload or KitReturn(), current_user), "Kit marked returned")
