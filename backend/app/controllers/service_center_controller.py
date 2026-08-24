from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.service_center_schema import ServiceCenterCreateRequest, ServiceCenterUpdateRequest
from app.services.audit_service import AuditService
from app.services.service_center_service import ServiceCenterService


class ServiceCenterController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ServiceCenterService(db)
        self.audit = AuditService(db)

    async def list(self, pagination: PaginationParams, active_only: bool):
        items, total = await self.service.list_centers(pagination.page, pagination.page_size, pagination.search, active_only)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def get(self, center_id: str):
        return success(await self.service.get(center_id))

    async def find_for_pincode(self, pincode: str):
        return success(await self.service.find_for_pincode(pincode))

    async def create(self, current_user: CurrentUser, payload: ServiceCenterCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_SERVICE_CENTER", "service_centers", result["id"])
        return success(result, "Service center created successfully")

    async def update(self, current_user: CurrentUser, center_id: str, payload: ServiceCenterUpdateRequest):
        result = await self.service.update(center_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_SERVICE_CENTER", "service_centers", center_id)
        return success(result, "Service center updated successfully")

    async def delete(self, current_user: CurrentUser, center_id: str):
        await self.service.delete(center_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_SERVICE_CENTER", "service_centers", center_id)
        return success(None, "Service center deleted successfully")
