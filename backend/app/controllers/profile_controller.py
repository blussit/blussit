from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.profile_schema import (
    AddressCreateRequest,
    AddressUpdateRequest,
    VehicleCreateRequest,
    VehicleUpdateRequest,
)
from app.services.profile_service import AddressService, VehicleService


class VehicleController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = VehicleService(db)

    async def list(self, current_user: CurrentUser):
        return success(await self.service.list_my_vehicles(current_user.id))

    async def create(self, current_user: CurrentUser, payload: VehicleCreateRequest):
        return success(await self.service.create(current_user.id, payload), "Vehicle added successfully")

    async def update(self, current_user: CurrentUser, vehicle_id: str, payload: VehicleUpdateRequest):
        return success(await self.service.update(current_user.id, vehicle_id, payload), "Vehicle updated successfully")

    async def delete(self, current_user: CurrentUser, vehicle_id: str):
        await self.service.delete(current_user.id, vehicle_id)
        return success(None, "Vehicle removed successfully")


class AddressController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = AddressService(db)

    async def list(self, current_user: CurrentUser):
        return success(await self.service.list_my_addresses(current_user.id))

    async def create(self, current_user: CurrentUser, payload: AddressCreateRequest):
        return success(await self.service.create(current_user.id, payload), "Address added successfully")

    async def update(self, current_user: CurrentUser, address_id: str, payload: AddressUpdateRequest):
        return success(await self.service.update(current_user.id, address_id, payload), "Address updated successfully")

    async def delete(self, current_user: CurrentUser, address_id: str):
        await self.service.delete(current_user.id, address_id)
        return success(None, "Address removed successfully")
