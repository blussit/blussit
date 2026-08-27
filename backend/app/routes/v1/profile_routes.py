from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.profile_controller import AddressController, VehicleController
from app.core.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.profile_schema import (
    AddressCreateRequest,
    AddressUpdateRequest,
    RegistrationCheckRequest,
    VehicleCreateRequest,
    VehicleUpdateRequest,
)

vehicle_router = APIRouter(prefix="/vehicles", tags=["Vehicles"])
address_router = APIRouter(prefix="/addresses", tags=["Addresses"])


@vehicle_router.get("")
async def list_vehicles(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await VehicleController(db).list(current_user)


@vehicle_router.post("/check-registration")
async def check_vehicle_registration(
    payload: RegistrationCheckRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Pre-check before actually adding a vehicle — lets the frontend show
    the "already registered with another account — Continue/Cancel"
    confirmation before submitting."""
    return await VehicleController(db).check_registration(current_user, payload)


@vehicle_router.post("")
async def create_vehicle(
    payload: VehicleCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await VehicleController(db).create(current_user, payload)


@vehicle_router.put("/{vehicle_id}")
async def update_vehicle(
    vehicle_id: str,
    payload: VehicleUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await VehicleController(db).update(current_user, vehicle_id, payload)


@vehicle_router.delete("/{vehicle_id}")
async def delete_vehicle(
    vehicle_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await VehicleController(db).delete(current_user, vehicle_id)


@address_router.get("")
async def list_addresses(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AddressController(db).list(current_user)


@address_router.post("")
async def create_address(
    payload: AddressCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AddressController(db).create(current_user, payload)


@address_router.put("/{address_id}")
async def update_address(
    address_id: str,
    payload: AddressUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AddressController(db).update(current_user, address_id, payload)


@address_router.delete("/{address_id}")
async def delete_address(
    address_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AddressController(db).delete(current_user, address_id)
