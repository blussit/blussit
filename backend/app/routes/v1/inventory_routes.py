from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.inventory_controller import InventoryController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_captain, require_manager_or_admin
from app.schemas.inventory_schema import InventoryAdjustRequest, InventoryCreateRequest, InventoryUpdateRequest

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("/my-center", dependencies=[Depends(require_captain)])
async def list_my_center_inventory(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    from app.repositories.user_repository import UserRepository
    from app.core.exceptions import BadRequestException

    user = await UserRepository(db).find_by_id(current_user.id)
    if not user or not user.get("service_center_id"):
        raise BadRequestException("You are not assigned to a service center")
    items, total = await InventoryController(db).service.list_for_center(user["service_center_id"], 1, 100)
    from app.core.responses import success
    return success(items)


@router.get("/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_for_center(
    service_center_id: str,
    low_stock_only: bool = False,
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await InventoryController(db).list_for_center(current_user, service_center_id, pagination, low_stock_only)


@router.post("", dependencies=[Depends(require_manager_or_admin)])
async def create_item(payload: InventoryCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await InventoryController(db).create(current_user, payload)


@router.put("/{item_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_item(item_id: str, payload: InventoryUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await InventoryController(db).update(current_user, item_id, payload)


@router.post("/{item_id}/adjust", dependencies=[Depends(require_manager_or_admin)])
async def adjust_item(item_id: str, payload: InventoryAdjustRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await InventoryController(db).adjust(current_user, item_id, payload)


@router.delete("/{item_id}", dependencies=[Depends(require_manager_or_admin)])
async def delete_item(item_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await InventoryController(db).delete(current_user, item_id)
