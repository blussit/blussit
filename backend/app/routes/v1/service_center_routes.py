from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.controllers.service_center_controller import ServiceCenterController
from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin, require_manager_or_admin
from app.core.responses import success
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest
from app.services.audit_service import AuditService
from app.services.booking_service import BookingService
from app.services.capacity_policy_service import CapacityPolicyService

router = APIRouter(prefix="/service-centers", tags=["Service Centers"])


class SlotCapacityUpdateRequest(BaseModel):
    date: str
    slot_key: str
    capacity: int | None = None
    is_closed: bool | None = None


class CapacityPolicyScheduleRequest(BaseModel):
    effective_date: str
    max_bookings_per_day: int
    # None or {} -> auto-distribute evenly across the center's slots.
    slot_distribution: dict[str, int] | None = None
    note: str | None = None


@router.get("", dependencies=[Depends(require_admin)])
async def list_centers(active_only: bool = False, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceCenterController(db).list(pagination, active_only)


@router.get("/lookup")
async def lookup_by_pincode(pincode: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceCenterController(db).find_for_pincode(pincode)


@router.get("/{center_id}/slots")
async def available_slots(center_id: str, date: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — the booking form needs this to render the slot picker.
    Deliberately never returns raw capacity, only the exact wording the
    customer UI shows (see BookingService.available_slots)."""
    return success(await BookingService(db).available_slots(center_id, date))


@router.get("/{center_id}/slot-capacity", dependencies=[Depends(require_manager_or_admin)])
async def admin_slot_capacity(
    center_id: str,
    date: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Staff-facing — unlike the public /slots endpoint, this one shows
    raw capacity/booked/remaining numbers. A manager only sees their own
    center; admin can see any."""
    if current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, center_id)
    return success(await BookingService(db).admin_slot_capacity(center_id, date))


@router.put("/{center_id}/slot-capacity", dependencies=[Depends(require_manager_or_admin)])
async def set_slot_capacity(
    center_id: str,
    payload: SlotCapacityUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, center_id)
    result = await BookingService(db).set_slot_capacity(center_id, payload.date, payload.slot_key, payload.capacity, payload.is_closed)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_SLOT_CAPACITY", "bookings", center_id,
        {"date": payload.date, "slot_key": payload.slot_key, "capacity": payload.capacity, "is_closed": payload.is_closed},
    )
    return success(result, "Slot capacity updated")


@router.get("/{center_id}/capacity-policy", dependencies=[Depends(require_manager_or_admin)])
async def capacity_policy_overview(
    center_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The Capacity page's header — today's active policy (daily max +
    per-slot distribution) and the next scheduled future change, if any.
    Section 1/4 of the ops spec: capacity's single source of truth."""
    if current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, center_id)
    return success(await CapacityPolicyService(db).overview(center_id))


@router.get("/{center_id}/capacity-policy/history", dependencies=[Depends(require_manager_or_admin)])
async def capacity_policy_history(
    center_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, center_id)
    return success(await CapacityPolicyService(db).list_history(center_id))


@router.post("/{center_id}/capacity-policy", dependencies=[Depends(require_manager_or_admin)])
async def schedule_capacity_policy(
    center_id: str,
    payload: CapacityPolicyScheduleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Schedule (or, for a still-future/today's date, edit in place) a
    capacity change — 'Apply immediately' is just effective_date = today;
    'Apply from a future date' is any later date. See
    CapacityPolicyService.schedule_change for the full validation rules
    (slot totals must equal the daily max, no negative capacity, no past
    effective dates)."""
    result = await CapacityPolicyService(db).schedule_change(
        center_id, payload.effective_date, payload.max_bookings_per_day, payload.slot_distribution,
        current_user.id, current_user.role, current_user.service_center_id, payload.note,
    )
    await AuditService(db).log_action(
        current_user.id, current_user.role, "SCHEDULE_CAPACITY_POLICY", "service_centers", center_id,
        {"effective_date": payload.effective_date, "max_bookings_per_day": payload.max_bookings_per_day},
    )
    return success(result, "Capacity change saved")


@router.delete("/{center_id}/capacity-policy/{change_id}", dependencies=[Depends(require_manager_or_admin)])
async def cancel_capacity_policy(
    center_id: str,
    change_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    await CapacityPolicyService(db).cancel_scheduled_change(center_id, change_id, current_user.id, current_user.role, current_user.service_center_id)
    await AuditService(db).log_action(current_user.id, current_user.role, "CANCEL_CAPACITY_POLICY", "service_centers", center_id, {"change_id": change_id})
    return success(None, "Scheduled capacity change cancelled")


@router.get("/{center_id}", dependencies=[Depends(require_manager_or_admin)])
async def get_center(center_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Manager/admin — a manager needs this for their own center (e.g. its
    working hours, to bound the reschedule time picker), nothing here is
    sensitive enough to restrict to admin-only."""
    return await ServiceCenterController(db).get(center_id)


@router.post("", dependencies=[Depends(require_admin)])
async def create_center(
    payload: ServiceCenterCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ServiceCenterController(db).create(current_user, payload)


@router.put("/{center_id}", dependencies=[Depends(require_admin)])
async def update_center(
    center_id: str,
    payload: ServiceCenterUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ServiceCenterController(db).update(current_user, center_id, payload)


@router.delete("/{center_id}", dependencies=[Depends(require_admin)])
async def delete_center(
    center_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ServiceCenterController(db).delete(current_user, center_id)
