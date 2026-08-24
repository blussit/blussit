from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_captain, require_manager_or_admin
from app.core.responses import paginated, success
from app.services.staff_directory_service import StaffDirectoryService

router = APIRouter(prefix="/staff", tags=["Staff Directory"])


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


@router.get("/captains/{captain_id}/performance", dependencies=[Depends(require_manager_or_admin)])
async def captain_performance(captain_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await StaffDirectoryService(db).captain_performance(captain_id))


@router.get("/my-performance", dependencies=[Depends(require_captain)])
async def my_performance(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await StaffDirectoryService(db).captain_performance(current_user.id))
