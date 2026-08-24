from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintUpdateRequest
from app.services.complaint_service import ComplaintService


class ComplaintController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ComplaintService(db)

    async def create(self, current_user: CurrentUser, payload: ComplaintCreateRequest):
        result = await self.service.create(current_user.id, payload)
        return success(result, "Complaint submitted successfully")

    async def list_mine(self, current_user: CurrentUser, pagination: PaginationParams):
        items, total = await self.service.list_for_customer(current_user.id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_for_center(self, current_user: CurrentUser, service_center_id: str, status: str | None, pagination: PaginationParams):
        items, total = await self.service.list_for_center(
            service_center_id, status, pagination.page, pagination.page_size, current_user.role, current_user.service_center_id
        )
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_all(self, filters: dict, pagination: PaginationParams):
        items, total = await self.service.list_all(filters, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def update(self, current_user: CurrentUser, complaint_id: str, payload: ComplaintUpdateRequest):
        result = await self.service.update(complaint_id, payload, current_user.id)
        return success(result, "Complaint updated successfully")
