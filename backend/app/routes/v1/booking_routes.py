from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.responses import success

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
    BookingGroupCreateRequest,
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    PhotoCaptureRequest,
    PriorityUpdateRequest,
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


@router.patch("/{booking_id}/priority")
async def update_priority(
    booking_id: str, payload: PriorityUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Manager/admin can set this on any booking in their own center; a
    captain only on their own assigned booking — enforced in the service
    layer (see BookingService.update_priority), not here, since a customer
    calling this correctly falls through to the same "not your center"
    rejection everyone else's center-scoping already goes through."""
    return await BookingController(db).update_priority(current_user, booking_id, payload)


@router.post("/{booking_id}/before-photo", dependencies=[Depends(require_captain)])
async def capture_before_photo(booking_id: str, payload: PhotoCaptureRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).capture_before_photo(current_user, booking_id, payload)


@router.post("/{booking_id}/after-photo", dependencies=[Depends(require_captain)])
async def capture_after_photo(booking_id: str, payload: PhotoCaptureRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).capture_after_photo(current_user, booking_id, payload)


@router.get("/group/{booking_group_id}")
async def get_booking_group(booking_group_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Every vehicle on one visit. A customer sees their own; staff see one
    their center serves."""
    return await BookingController(db).get_group(current_user, booking_group_id)


@router.post("/group/{booking_group_id}/switch-to-cash", dependencies=[Depends(require_customer)])
async def switch_group_to_cash(booking_group_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """One decision for the whole visit — the customer made one booking."""
    return await BookingController(db).switch_group_to_cash(current_user, booking_group_id)


@router.post("/group/{booking_group_id}/cancel")
async def cancel_booking_group(booking_group_id: str, payload: BookingCancelRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Cancelling a visit cancels every vehicle on it."""
    return await BookingController(db).cancel_group(current_user, booking_group_id, payload)


@router.post("/group/{booking_group_id}/assign-captain", dependencies=[Depends(require_manager_or_admin)])
async def assign_captain_to_group(booking_group_id: str, payload: BookingAssignCaptainRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """One captain takes every vehicle on a visit — they're worked back to
    back at one address, so splitting them would send two people to one gate."""
    return await BookingController(db).assign_group(current_user, booking_group_id, payload)


@router.post("/group", dependencies=[Depends(require_customer)])
async def create_booking_group(payload: BookingGroupCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Several of the caller's own vehicles washed on ONE visit — one
    address, one slot, one captain, one payment. Takes a single slot seat
    however many cars are on it, because it's a single trip."""
    return await BookingController(db).create_group(current_user, payload)


@router.post("/{booking_id}/switch-to-cash", dependencies=[Depends(require_customer)])
async def switch_to_cash(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """"I couldn't finish paying online — let me pay the captain instead."
    Confirms the caller's own still-unpaid booking as a cash booking, so an
    abandoned or failed online payment doesn't cost them the slot."""
    return await BookingController(db).switch_to_cash(current_user, booking_id)


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, payload: BookingCancelRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await BookingController(db).cancel(current_user, booking_id, payload)


@router.post("/{booking_id}/reschedule", dependencies=[Depends(get_current_user)])
async def reschedule_booking(booking_id: str, payload: BookingRescheduleRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Customers reschedule their own bookings; managers/admins can also reschedule
    as a remediation path when a captain issue was flagged (see resolve_issue)."""
    return await BookingController(db).reschedule(current_user, booking_id, payload)



class SlotHoldRequest(BaseModel):
    """Unauthenticated by design — guests hold a slot while filling the
    wizard. The server validates the center/date/slot against real
    generated slots (see BookingService.hold_slot) precisely because
    nothing about this payload can be trusted."""
    holder_key: str = Field(min_length=8, max_length=64)
    service_center_id: str
    date: str
    slot_key: str = Field(max_length=20)


@router.post("/hold")
async def hold_slot(payload: SlotHoldRequest, db=Depends(get_db)):
    from app.services.booking_service import BookingService

    return success(await BookingService(db).hold_slot(payload.holder_key, payload.service_center_id, payload.date, payload.slot_key))


@router.post("/hold/release")
async def release_hold(payload: SlotHoldRequest, db=Depends(get_db)):
    from app.services.booking_service import BookingService

    return success(await BookingService(db).release_hold(payload.holder_key, payload.service_center_id, payload.date, payload.slot_key))


@router.get("/{booking_id}/travel-status")
async def travel_status(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Store->customer + captain->customer distance/ETA — powers the
    customer's "captain is ~N min away" and staff dispatch context."""
    from app.services.booking_service import BookingService

    return success(await BookingService(db).travel_status(booking_id, current_user.id, current_user.role, current_user.service_center_id))
