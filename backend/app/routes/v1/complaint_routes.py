from typing import Optional

from fastapi import APIRouter, Depends
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
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintReplyRequest, ComplaintUpdateRequest

router = APIRouter(prefix="/complaints", tags=["Complaints"])


@router.post("", dependencies=[Depends(require_customer)])
async def create_complaint(payload: ComplaintCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).create(current_user, payload)


@router.get("/my", dependencies=[Depends(require_customer)])
async def list_my_complaints(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).list_mine(current_user, pagination)


@router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_for_center(
    service_center_id: str,
    status: Optional[str] = None,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ComplaintController(db).list_for_center(current_user, service_center_id, status, pagination)


@router.get("", dependencies=[Depends(require_admin)])
async def list_all(status: Optional[str] = None, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    filters = {"status": status} if status else {}
    return await ComplaintController(db).list_all(filters, pagination)


@router.put("/{complaint_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_complaint(complaint_id: str, payload: ComplaintUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComplaintController(db).update(current_user, complaint_id, payload)


@router.post("/{complaint_id}/reply")
async def reply_to_complaint(complaint_id: str, payload: ComplaintReplyRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Staff replies stay center-scoped; a CUSTOMER may reply on their own
    thread too (ownership + no-status-change enforced in the service) —
    support used to be one-way, readable but unanswerable."""
    return await ComplaintController(db).reply(current_user, complaint_id, payload)
