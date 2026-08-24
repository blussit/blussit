from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.user_schema import AdminUserUpdateRequest, UserUpdateRequest
from app.services.audit_service import AuditService
from app.services.user_service import UserService


class UserController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = UserService(db)
        self.audit = AuditService(db)

    async def update_my_profile(self, current_user: CurrentUser, payload: UserUpdateRequest):
        result = await self.service.update_profile(current_user.id, payload)
        return success(result, "Profile updated successfully")

    async def list_users(self, role: str | None, pagination: PaginationParams):
        items, total = await self.service.list_users(role, pagination.page, pagination.page_size, pagination.search)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def get_user(self, user_id: str):
        result = await self.service.get_by_id(user_id)
        return success(result)

    async def admin_update_user(self, current_user: CurrentUser, user_id: str, payload: AdminUserUpdateRequest):
        result = await self.service.admin_update_user(user_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_USER", "users", user_id, payload.model_dump(exclude_unset=True))
        return success(result, "User updated successfully")

    async def deactivate_user(self, current_user: CurrentUser, user_id: str):
        result = await self.service.deactivate_user(user_id)
        await self.audit.log_action(current_user.id, current_user.role, "SUSPEND_USER", "users", user_id)
        return success(result, "User suspended successfully")

    async def delete_user(self, current_user: CurrentUser, user_id: str):
        await self.service.delete_user(user_id, current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_USER", "users", user_id)
        return success(None, "User deleted successfully")
