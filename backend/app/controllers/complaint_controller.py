from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintReplyRequest, ComplaintUpdateRequest
from app.services.audit_service import AuditService
from app.services.complaint_service import ComplaintService


class ComplaintController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ComplaintService(db)
        self.audit = AuditService(db)

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
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_COMPLAINT", "complaints", complaint_id,
            {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None},
        )
        return success(result, "Complaint updated successfully")

    async def reply(self, current_user: CurrentUser, complaint_id: str, payload: ComplaintReplyRequest):
        result = await self.service.add_reply(
            complaint_id, current_user.id, current_user.role, current_user.service_center_id, payload.message,
            payload.status.value if payload.status else None,
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "REPLY_COMPLAINT", "complaints", complaint_id, {"message": payload.message, "status": payload.status},
        )
        return success(result, "Reply added")
