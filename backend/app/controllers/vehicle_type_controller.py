from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.vehicle_type_schema import VehicleTypeCreateRequest, VehicleTypeUpdateRequest
from app.services.audit_service import AuditService, field_changes
from app.services.vehicle_type_service import VehicleTypeService


class VehicleTypeController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = VehicleTypeService(db)
        self.audit = AuditService(db)

    async def list(self, active_only: bool):
        return success(await self.service.list_all(active_only))

    async def create(self, current_user: CurrentUser, payload: VehicleTypeCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_VEHICLE_TYPE", "vehicle_types", result["id"])
        return success(result, "Vehicle type created successfully")

    async def update(self, current_user: CurrentUser, vehicle_type_id: str, payload: VehicleTypeUpdateRequest):
        before = await self.service.repo.find_by_id(vehicle_type_id)
        result = await self.service.update(vehicle_type_id, payload)
        # Before/after for every field that really changed (ADM-08).
        changes = field_changes(before, result, [*payload.model_dump(exclude_unset=True), "slug"])
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_VEHICLE_TYPE", "vehicle_types", vehicle_type_id, {"changes": changes})
        return success(result, "Vehicle type updated successfully")

    async def delete(self, current_user: CurrentUser, vehicle_type_id: str):
        before = await self.service.repo.find_by_id(vehicle_type_id)
        await self.service.delete(vehicle_type_id)
        await self.audit.log_action(
            current_user.id, current_user.role, "DELETE_VEHICLE_TYPE", "vehicle_types", vehicle_type_id, {"name": (before or {}).get("name")},
        )
        return success(None, "Vehicle type removed successfully")
