from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.service_center_controller import ServiceCenterController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin, require_manager_or_admin
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest

router = APIRouter(prefix="/service-centers", tags=["Service Centers"])


@router.get("", dependencies=[Depends(require_admin)])
async def list_centers(active_only: bool = False, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceCenterController(db).list(pagination, active_only)


@router.get("/lookup")
async def lookup_by_pincode(pincode: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceCenterController(db).find_for_pincode(pincode)


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
