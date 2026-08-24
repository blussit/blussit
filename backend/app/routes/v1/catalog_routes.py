from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.catalog_controller import CategoryController, ComboOfferController, ServiceController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin
from app.schemas.catalog_schema import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    ComboOfferCreateRequest,
    ComboOfferUpdateRequest,
    ServiceCreateRequest,
    ServiceUpdateRequest,
)

category_router = APIRouter(prefix="/categories", tags=["Categories"])
service_router = APIRouter(prefix="/services", tags=["Services"])
combo_router = APIRouter(prefix="/combo-offers", tags=["Combo Offers"])


@category_router.get("")
async def list_categories(active_only: bool = False, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CategoryController(db).list(active_only)


@category_router.post("", dependencies=[Depends(require_admin)])
async def create_category(payload: CategoryCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CategoryController(db).create(current_user, payload)


@category_router.put("/{category_id}", dependencies=[Depends(require_admin)])
async def update_category(category_id: str, payload: CategoryUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CategoryController(db).update(current_user, category_id, payload)


@category_router.delete("/{category_id}", dependencies=[Depends(require_admin)])
async def delete_category(category_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CategoryController(db).delete(current_user, category_id)


@service_router.get("")
async def list_services(
    category_id: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    active_only: bool = True,
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await ServiceController(db).list(pagination, category_id, vehicle_type, active_only)


@service_router.get("/{service_id}")
async def get_service(service_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceController(db).get(service_id)


@service_router.post("", dependencies=[Depends(require_admin)])
async def create_service(payload: ServiceCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceController(db).create(current_user, payload)


@service_router.put("/{service_id}", dependencies=[Depends(require_admin)])
async def update_service(service_id: str, payload: ServiceUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceController(db).update(current_user, service_id, payload)


@service_router.delete("/{service_id}", dependencies=[Depends(require_admin)])
async def delete_service(service_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ServiceController(db).delete(current_user, service_id)


@combo_router.get("")
async def list_combo_offers(active_only: bool = False, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComboOfferController(db).list(active_only)


@combo_router.get("/{combo_id}")
async def get_combo_offer(combo_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComboOfferController(db).get(combo_id)


@combo_router.post("", dependencies=[Depends(require_admin)])
async def create_combo_offer(payload: ComboOfferCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComboOfferController(db).create(current_user, payload)


@combo_router.put("/{combo_id}", dependencies=[Depends(require_admin)])
async def update_combo_offer(combo_id: str, payload: ComboOfferUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComboOfferController(db).update(current_user, combo_id, payload)


@combo_router.delete("/{combo_id}", dependencies=[Depends(require_admin)])
async def delete_combo_offer(combo_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await ComboOfferController(db).delete(current_user, combo_id)
