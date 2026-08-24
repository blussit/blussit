from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.repositories.user_repository import UserRepository
from app.schemas.user_schema import AdminUserUpdateRequest, UserPublic, UserUpdateRequest


class UserService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = UserRepository(db)

    async def get_by_id(self, user_id: str) -> dict:
        user = await self.repo.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(user).model_dump()

    async def update_profile(self, user_id: str, payload: UserUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(user_id, data)
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def list_users(self, role: str | None, page: int, page_size: int, search: str | None):
        filters = {"role": role} if role else {}
        if search:
            from app.repositories.base_repository import build_search_filter
            filters.update(build_search_filter(search, ["full_name", "email", "phone"]))
        items, total = await self.repo.find_many(filters, page=page, page_size=page_size)
        return [UserPublic.from_doc(u).model_dump() for u in items], total

    async def admin_update_user(self, user_id: str, payload: AdminUserUpdateRequest) -> dict:
        data = {k: v.value if hasattr(v, "value") else v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(user_id, data)
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def deactivate_user(self, user_id: str) -> dict:
        updated = await self.repo.update_by_id(user_id, {"status": "suspended"})
        if not updated:
            raise NotFoundException("User not found")
        return UserPublic.from_doc(updated).model_dump()

    async def delete_user(self, user_id: str, deleted_by: str) -> bool:
        return await self.repo.soft_delete(user_id, deleted_by)
