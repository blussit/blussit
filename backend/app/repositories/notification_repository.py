from app.repositories.base_repository import BaseRepository


class NotificationRepository(BaseRepository):
    collection_name = "notifications"

    async def list_for_user(self, user_id: str, page: int, page_size: int, unread_only: bool = False):
        filters: dict = {"user_id": user_id}
        if unread_only:
            filters["is_read"] = False
        return await self.find_many(filters, page=page, page_size=page_size)

    async def unread_count(self, user_id: str) -> int:
        return await self.count({"user_id": user_id, "is_read": False})

    async def mark_all_read(self, user_id: str) -> None:
        await self.collection.update_many({"user_id": user_id, "is_read": False}, {"$set": {"is_read": True}})
