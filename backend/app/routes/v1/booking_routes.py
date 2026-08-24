from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.booking_controller import BookingController
from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_admin,
    require_captain,
    require_customer,
    require_manager_or_admin,
)
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    PhotoCaptureRequest,
    ReassignCaptainRequest,
    ReportRiskRequest,
    ResolveIssueRequest,
    VerifyVehicleRequest,
)

router = APIRouter(prefix="/bookings", tags=["Bookings"])


@router.post("", dependencies=[Depends(require_customer)])
async def create_booking(payload: BookingCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).create(current_user, payload)


@router.post("/manager-create", dependencies=[Depends(require_manager_or_admin)])
async def manager_create_booking(
    payload: ManagerBookingCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Manager/admin books on behalf of a customer (new or existing) —
    reuses the exact same pricing/fraud/scheduling logic as self-service
    booking, see BookingService.create_booking_for_customer."""
    return await BookingController(db).manager_create(current_user, payload)


@router.get("/my")
async def list_my_bookings(status: Optional[str] = None, pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).list_my_bookings(current_user, status, pagination)


@router.get("/my-jobs", dependencies=[Depends(require_captain)])
async def list_my_jobs(status: Optional[str] = None, pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).list_my_jobs(current_user, status, pagination)


@router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_for_center(
    service_center_id: str,
    status: Optional[str] = None,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await BookingController(db).list_for_center(current_user, service_center_id, status, pagination)


@router.get("/center/{service_center_id}/subscribers", dependencies=[Depends(require_manager_or_admin)])
async def list_subscribers_for_center(
    service_center_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Who at this store has an active plan and has actually used it — the
    manager-facing view behind 'who purchased which plan from my store'."""
    return await BookingController(db).list_subscribers_for_center(current_user, service_center_id)


@router.get("", dependencies=[Depends(require_admin)])
async def list_all(status: Optional[str] = None, service_center_id: Optional[str] = None, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    filters: dict = {}
    if status:
        filters["status"] = status
    if service_center_id:
        filters["service_center_id"] = service_center_id
    return await BookingController(db).list_all(filters, pagination)


@router.get("/{booking_id}")
async def get_booking(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).get(current_user, booking_id)


@router.post("/{booking_id}/assign-captain", dependencies=[Depends(require_manager_or_admin)])
async def assign_captain(booking_id: str, payload: BookingAssignCaptainRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).assign_captain(current_user, booking_id, payload)


@router.post("/{booking_id}/reassign-captain", dependencies=[Depends(require_manager_or_admin)])
async def reassign_captain(booking_id: str, payload: ReassignCaptainRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).reassign_captain(current_user, booking_id, payload)


@router.post("/{booking_id}/captain-cancel", dependencies=[Depends(require_captain)])
async def captain_cancel(booking_id: str, payload: CaptainCancelRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).captain_cancel(current_user, booking_id, payload)


@router.post("/{booking_id}/heading", dependencies=[Depends(require_captain)])
async def start_heading(booking_id: str, payload: HeadingRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).start_heading(current_user, booking_id, payload)


@router.post("/{booking_id}/verify-vehicle", dependencies=[Depends(require_captain)])
async def verify_vehicle(booking_id: str, payload: VerifyVehicleRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).verify_vehicle(current_user, booking_id, payload)


@router.get("/{booking_id}/eligible-captains", dependencies=[Depends(require_manager_or_admin)])
async def eligible_captains(
    booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """For the assign/reassign-captain picker — every captain at this
    booking's service center with whether they can actually take it and why
    not if they can't (schedule conflict, wallet balance), so the manager
    sees availability before assigning instead of only finding out via a
    failed submit."""
    return await BookingController(db).eligible_captains(current_user, db, booking_id)


@router.post("/{booking_id}/report-risk", dependencies=[Depends(require_captain)])
async def report_risk(booking_id: str, payload: ReportRiskRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Captain self-reports they may run late for this (still-assigned,
    not-yet-started) booking because their current job is running long."""
    return await BookingController(db).report_risk(current_user, booking_id, payload)


@router.post("/{booking_id}/resolve-issue", dependencies=[Depends(require_manager_or_admin)])
async def resolve_issue(booking_id: str, payload: ResolveIssueRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Clears a flagged issue (captain not reached, captain delay, etc.) without
    necessarily reassigning/rescheduling — e.g. the manager called the captain
    and confirmed things are fine."""
    return await BookingController(db).resolve_issue(current_user, booking_id, payload)


@router.post("/{booking_id}/before-photo", dependencies=[Depends(require_captain)])
async def capture_before_photo(booking_id: str, payload: PhotoCaptureRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).capture_before_photo(current_user, booking_id, payload)


@router.post("/{booking_id}/after-photo", dependencies=[Depends(require_captain)])
async def capture_after_photo(booking_id: str, payload: PhotoCaptureRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).capture_after_photo(current_user, booking_id, payload)


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, payload: BookingCancelRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).cancel(current_user, booking_id, payload)


@router.post("/{booking_id}/reschedule", dependencies=[Depends(get_current_user)])
async def reschedule_booking(booking_id: str, payload: BookingRescheduleRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Customers reschedule their own bookings; managers/admins can also reschedule
    as a remediation path when a captain issue was flagged (see resolve_issue)."""
    return await BookingController(db).reschedule(current_user, booking_id, payload)


@router.post("/{booking_id}/rebook", dependencies=[Depends(require_customer)])
async def rebook(booking_id: str, scheduled_date: datetime, scheduled_slot: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).rebook(current_user, booking_id, scheduled_date, scheduled_slot)
