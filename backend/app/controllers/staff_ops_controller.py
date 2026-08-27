from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.staff_ops_schema import CheckInRequest, LeaveRequestCreate, LeaveReviewRequest
from app.services.audit_service import AuditService
from app.services.staff_ops_service import AttendanceService, LeaveService


class AttendanceController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = AttendanceService(db)

    async def check_in(self, current_user: CurrentUser, payload: CheckInRequest):
        return success(await self.service.check_in(current_user.id, payload), "Checked in successfully")

    async def check_out(self, current_user: CurrentUser):
        return success(await self.service.check_out(current_user.id), "Checked out successfully")

    async def list_mine(self, current_user: CurrentUser, pagination: PaginationParams):
        items, total = await self.service.list_for_captain(current_user.id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)


class LeaveController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = LeaveService(db)
        self.audit = AuditService(db)

    async def request_leave(self, current_user: CurrentUser, payload: LeaveRequestCreate):
        return success(await self.service.request_leave(current_user.id, payload), "Leave request submitted")

    async def list_mine(self, current_user: CurrentUser, pagination: PaginationParams):
        items, total = await self.service.list_for_captain(current_user.id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_pending_for_center(self, current_user: CurrentUser, service_center_id: str, pagination: PaginationParams):
        ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
        items, total = await self.service.list_pending_for_center(service_center_id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def review(self, current_user: CurrentUser, leave_id: str, payload: LeaveReviewRequest):
        result = await self.service.review(leave_id, payload, current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "REVIEW_LEAVE_REQUEST", "leave_requests", leave_id, {"status": payload.status})
        return success(result, "Leave request reviewed")
