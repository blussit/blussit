from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.complaint_controller import ComplaintController
from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_admin,
    require_customer,
    require_manager_or_admin,
)
from app.models.enums import ComplaintStatus
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintReplyRequest, ComplaintUpdateRequest

router = APIRouter(prefix="/complaints", tags=["Complaints"])


@router.post("", dependencies=[Depends(require_customer)])
async def create_complaint(payload: ComplaintCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).create(current_user, payload)


@router.get("/my", dependencies=[Depends(require_customer)])
async def list_my_complaints(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).list_mine(current_user, pagination)


_OID = r"^[0-9a-fA-F]{24}$"
# A malformed id is a 422 at the door, never an exception deeper down.
ComplaintId = Annotated[str, Path(pattern=_OID)]


@router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_for_center(
    service_center_id: Annotated[str, Path(pattern=_OID)],
    status: Optional[ComplaintStatus] = None,
    category: Optional[str] = Query(None, pattern=r"^(society|booking)$"),
    society_id: Optional[str] = Query(None, pattern=_OID),
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """`category=society` = society residents' issues (optionally one
    `society_id`); `category=booking` = everything else."""
    return await ComplaintController(db).list_for_center(
        current_user, service_center_id, status.value if status else None, pagination, extra=_category_filter(category, society_id),
    )


def _category_filter(category: Optional[str], society_id: Optional[str]) -> dict:
    if society_id:
        return {"society_id": society_id}
    if category == "society":
        return {"category": "society"}
    if category == "booking":
        return {"category": {"$ne": "society"}}
    return {}


@router.get("", dependencies=[Depends(require_admin)])
async def list_all(
    status: Optional[str] = None,
    service_center_id: Optional[str] = None,
    period: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    category: Optional[str] = Query(None, pattern=r"^(society|booking)$"),
    society_id: Optional[str] = Query(None, pattern=_OID),
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """`search` (PaginationParams) matches subject, booking number, or the
    customer's name/phone."""
    filters: dict = {"status": status} if status else {}
    if service_center_id:
        filters["service_center_id"] = service_center_id
    if period or (start and end):
        from app.services.kpi_service import resolve_period

        s, e, _ps, _pe = resolve_period(period, start, end)
        filters["created_at"] = {"$gte": s, "$lt": e}
    filters.update(_category_filter(category, society_id))
    return await ComplaintController(db).list_all(filters, pagination)


@router.get("/{complaint_id}")
async def get_complaint(complaint_id: ComplaintId, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """One ticket with its full, current thread — the customer who raised
    it, a manager of its center, or an admin."""
    return await ComplaintController(db).get_one(current_user, complaint_id)


@router.put("/{complaint_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_complaint(complaint_id: ComplaintId, payload: ComplaintUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).update(current_user, complaint_id, payload)


@router.post("/{complaint_id}/reply")
async def reply_to_complaint(complaint_id: ComplaintId, payload: ComplaintReplyRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Staff replies stay center-scoped; a CUSTOMER may reply on their own
    thread too (ownership + no-status-change enforced in the service) —
    support used to be one-way, readable but unanswerable."""
    return await ComplaintController(db).reply(current_user, complaint_id, payload)
