from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.profile_schema import (
    AddressCreateRequest,
    AddressUpdateRequest,
    RegistrationCheckRequest,
    VehicleCreateRequest,
    VehicleUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.profile_service import AddressService, VehicleService


class VehicleController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = VehicleService(db)
        self.audit = AuditService(db)

    async def list(self, current_user: CurrentUser):
        return success(await self.service.list_my_vehicles(current_user.id))

    async def check_registration(self, current_user: CurrentUser, payload: RegistrationCheckRequest):
        return success(await self.service.check_registration(current_user.id, payload.registration_number))

    async def create(self, current_user: CurrentUser, payload: VehicleCreateRequest):
        result = await self.service.create(current_user.id, payload, current_user.role)
        await self.audit.log_action(
            current_user.id, current_user.role, "CREATE_VEHICLE", "vehicles", result["id"],
            {"registration_number": payload.registration_number},
        )
        return success(result, "Vehicle added successfully")

    async def update(self, current_user: CurrentUser, vehicle_id: str, payload: VehicleUpdateRequest):
        result = await self.service.update(current_user.id, vehicle_id, payload, current_user.role)
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_VEHICLE", "vehicles", vehicle_id,
            {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None},
        )
        return success(result, "Vehicle updated successfully")

    async def delete(self, current_user: CurrentUser, vehicle_id: str):
        await self.service.delete(current_user.id, vehicle_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_VEHICLE", "vehicles", vehicle_id)
        return success(None, "Vehicle removed successfully")


class AddressController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = AddressService(db)
        self.audit = AuditService(db)

    async def list(self, current_user: CurrentUser):
        return success(await self.service.list_my_addresses(current_user.id))

    async def create(self, current_user: CurrentUser, payload: AddressCreateRequest):
        result = await self.service.create(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_ADDRESS", "addresses", result["id"])
        return success(result, "Address added successfully")

    async def update(self, current_user: CurrentUser, address_id: str, payload: AddressUpdateRequest):
        result = await self.service.update(current_user.id, address_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_ADDRESS", "addresses", address_id)
        return success(result, "Address updated successfully")

    async def delete(self, current_user: CurrentUser, address_id: str):
        await self.service.delete(current_user.id, address_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_ADDRESS", "addresses", address_id)
        return success(None, "Address removed successfully")
