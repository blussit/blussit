from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.vehicle_type_controller import VehicleTypeController
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.schemas.vehicle_type_schema import VehicleTypeCreateRequest, VehicleTypeUpdateRequest

router = APIRouter(prefix="/vehicle-types", tags=["Vehicle Types"])


@router.get("")
async def list_vehicle_types(active_only: bool = False, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — every vehicle/booking/subscription picker across every role needs this list."""
    return await VehicleTypeController(db).list(active_only)


@router.post("", dependencies=[Depends(require_admin)])
async def create_vehicle_type(
    payload: VehicleTypeCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await VehicleTypeController(db).create(current_user, payload)


@router.put("/{vehicle_type_id}", dependencies=[Depends(require_admin)])
async def update_vehicle_type(
    vehicle_type_id: str,
    payload: VehicleTypeUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await VehicleTypeController(db).update(current_user, vehicle_type_id, payload)


@router.delete("/{vehicle_type_id}", dependencies=[Depends(require_admin)])
async def delete_vehicle_type(
    vehicle_type_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    return await VehicleTypeController(db).delete(current_user, vehicle_type_id)
