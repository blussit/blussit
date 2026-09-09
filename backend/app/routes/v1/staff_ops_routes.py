from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.staff_ops_controller import AttendanceController, LeaveController
from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_captain,
    require_manager_or_admin,
)
from app.schemas.staff_ops_schema import CheckInRequest, CheckOutRequest, LeaveRequestCreate, LeaveReviewRequest

attendance_router = APIRouter(prefix="/attendance", tags=["Attendance"], dependencies=[Depends(require_captain)])
leave_router = APIRouter(prefix="/leave-requests", tags=["Leave Requests"])


@attendance_router.post("/check-in")
async def check_in(payload: CheckInRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AttendanceController(db).check_in(current_user, payload)


@attendance_router.post("/check-out")
async def check_out(
    payload: CheckOutRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AttendanceController(db).check_out(current_user, payload)


@attendance_router.get("/my")
async def list_my_attendance(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AttendanceController(db).list_mine(current_user, pagination)


@leave_router.post("", dependencies=[Depends(require_captain)])
async def request_leave(payload: LeaveRequestCreate, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await LeaveController(db).request_leave(current_user, payload)


@leave_router.get("/my", dependencies=[Depends(require_captain)])
async def list_my_leave_requests(pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await LeaveController(db).list_mine(current_user, pagination)


@leave_router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_pending_leave_requests(
    service_center_id: str,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await LeaveController(db).list_pending_for_center(current_user, service_center_id, pagination)


@leave_router.put("/{leave_id}/review", dependencies=[Depends(require_manager_or_admin)])
async def review_leave_request(leave_id: str, payload: LeaveReviewRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await LeaveController(db).review(current_user, leave_id, payload)
