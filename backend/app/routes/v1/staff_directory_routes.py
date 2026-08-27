from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_captain, require_manager_or_admin
from app.core.exceptions import BadRequestException
from app.core.responses import paginated, success
from app.schemas.booking_schema import CaptainLocationPingRequest
from app.services.booking_service import BookingService
from app.services.staff_directory_service import StaffDirectoryService

router = APIRouter(prefix="/staff", tags=["Staff Directory"])


@router.post("/captains/location", dependencies=[Depends(require_captain)])
async def ping_captain_location(
    payload: CaptainLocationPingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Periodic location update sent by a captain's device while they have
    an active job — extends the three discrete GPS captures (heading,
    before/after photos) into continuous tracking FOR THE DURATION OF A
    JOB ONLY, never while off duty (see BookingService.has_active_job) —
    this is a deliberate boundary, not a limitation to work around."""
    booking_service = BookingService(db)
    if not await booking_service.has_active_job(current_user.id):
        raise BadRequestException("Location updates are only accepted while you have an active job.")
    await booking_service.update_captain_location(current_user.id, payload.latitude, payload.longitude)
    return success(None, "Location updated")


@router.get("/captains/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_captains_for_center(
    service_center_id: str,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    items, total = await StaffDirectoryService(db).list_captains_for_center(service_center_id, pagination.page, pagination.page_size)
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/captains/{captain_id}/attendance", dependencies=[Depends(require_manager_or_admin)])
async def captain_attendance(
    captain_id: str,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """A manager's own captain's check-in/check-out history — surfaced in
    the captain detail view alongside performance/bookings/reviews."""
    items, total = await StaffDirectoryService(db).captain_attendance(
        captain_id, current_user.role, current_user.service_center_id, pagination.page, pagination.page_size
    )
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/captains/{captain_id}/performance", dependencies=[Depends(require_manager_or_admin)])
async def captain_performance(
    captain_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    service_center_id: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if service_center_id:
        ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    return success(await StaffDirectoryService(db).captain_performance(captain_id, date_from, date_to, service_center_id))


@router.get("/my-performance", dependencies=[Depends(require_captain)])
async def my_performance(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await StaffDirectoryService(db).captain_performance(current_user.id))


@router.get("/captains/center/{service_center_id}/performance", dependencies=[Depends(require_manager_or_admin)])
async def captains_performance_for_center(
    service_center_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List-form KPI view — every captain at a center with their performance
    figures side by side, for a manager comparing their team (Section 11's
    filterable KPI matrix)."""
    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    service = StaffDirectoryService(db)
    captains, _ = await service.list_captains_for_center(service_center_id, 1, 200)
    results = []
    for captain in captains:
        perf = await service.captain_performance(captain["id"], date_from, date_to, service_center_id)
        results.append({"captain_id": captain["id"], "full_name": captain.get("full_name", ""), **perf})
    return success(results)
