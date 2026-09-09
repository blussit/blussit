from datetime import datetime

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_captain, require_manager_or_admin
from app.core.exceptions import BadRequestException, NotFoundException
from app.core.responses import paginated, success
from app.repositories.captain_location_repository import CaptainLocationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.booking_schema import CaptainLocationPingRequest
from app.schemas.staff_kyc_schema import KycReviewRequest, KycSubmitRequest
from app.services.booking_service import BookingService
from app.services.staff_directory_service import StaffDirectoryService
from app.services.staff_kyc_service import StaffKycService
from app.utils.timezone import from_stored

router = APIRouter(prefix="/staff", tags=["Staff Directory"])


@router.get("/my-kyc", dependencies=[Depends(require_captain)])
async def my_kyc(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await StaffKycService(db).get_my(current_user.id))


@router.put("/my-kyc", dependencies=[Depends(require_captain)])
async def submit_my_kyc(payload: KycSubmitRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await StaffKycService(db).submit(current_user.id, payload), "Documents submitted for verification")


@router.get("/captains/{captain_id}/kyc", dependencies=[Depends(require_manager_or_admin)])
async def captain_kyc(captain_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The manager's review view — the one list surface where full (unmasked)
    numbers are legitimate, since the manager is the verifier."""
    return success(await StaffKycService(db).get_for_review(captain_id, current_user.role, current_user.service_center_id))


@router.post("/captains/{captain_id}/kyc/review", dependencies=[Depends(require_manager_or_admin)])
async def review_captain_kyc(
    captain_id: str,
    payload: KycReviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await StaffKycService(db).review(captain_id, payload, current_user.id, current_user.role, current_user.service_center_id)
    return success(result, "Review saved")


@router.get("/captains/{captain_id}/locations", dependencies=[Depends(require_manager_or_admin)])
async def captain_location_trail(
    captain_id: str,
    since: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """GPS breadcrumb trail (last 30 days max — rows TTL out) for the
    manager's map/audit view. `since` is an ISO datetime."""
    captain = await UserRepository(db).find_by_id(captain_id)
    if not captain or captain.get("role") != "captain":
        raise NotFoundException("Captain not found")
    ensure_own_center(current_user.role, current_user.service_center_id, captain.get("service_center_id"))
    since_dt = datetime.fromisoformat(since) if since else None
    rows = await CaptainLocationRepository(db).list_for_captain(captain_id, since_dt)
    return success([
        {
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "source": r.get("source", "ping"),
            "booking_id": r.get("booking_id"),
            "at": from_stored(r["at"]).isoformat(),
        }
        for r in rows
    ])


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
    # Scope against the CAPTAIN's own center, not the optional filter — the
    # old check only ran when the caller volunteered a center id, so any
    # manager could read any captain's earnings/KPIs by simply omitting it.
    captain = await UserRepository(db).find_by_id(captain_id)
    if not captain or captain.get("role") != "captain":
        raise NotFoundException("Captain not found")
    ensure_own_center(current_user.role, current_user.service_center_id, captain.get("service_center_id"))
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
