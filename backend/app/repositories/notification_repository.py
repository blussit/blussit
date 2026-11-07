from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.repositories.base_repository import BaseRepository


class NotificationRepository(BaseRepository):
    collection_name = "notifications"

    async def mark_read_for_user(self, notification_id: str, user_id: str) -> dict | None:
        """Owner-scoped mark-read in ONE atomic filter — the ownership check
        and the write can never disagree, and a foreign id simply matches
        nothing (returns None) instead of leaking another user's document."""
        if not ObjectId.is_valid(notification_id):
            return None
        return await self.collection.find_one_and_update(
            {"_id": ObjectId(notification_id), "user_id": user_id, "is_deleted": {"$ne": True}},
            {"$set": {"is_read": True, "read_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )

    async def list_for_user(self, user_id: str, page: int, page_size: int, unread_only: bool = False):
        filters: dict = {"user_id": user_id}
        if unread_only:
            filters["is_read"] = False
        return await self.find_many(filters, page=page, page_size=page_size)

    async def unread_count(self, user_id: str) -> int:
        return await self.count({"user_id": user_id, "is_read": False})

    async def mark_all_read(self, user_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self.collection.update_many({"user_id": user_id, "is_read": False}, {"$set": {"is_read": True, "read_at": now}})

    async def _newest_read_ids(self, user_id: str, keep: int) -> list:
        """The `keep` newest alerts the user opened (not the ones the system cleared for them)."""
        rows = await self.collection.find(
            {"user_id": user_id, "is_read": True, "auto_cleared": {"$ne": True}, "is_deleted": {"$ne": True}}, {"_id": 1},
        ).sort("created_at", -1).limit(keep).to_list(keep)
        return [r["_id"] for r in rows]

    async def list_with_read(self, user_id: str, page: int, page_size: int):
        """Everything unread plus what he has read (not the ones the system cleared) — a
        manager's history view. prune_read keeps that short."""
        return await self.find_many(
            {"user_id": user_id, "$or": [{"is_read": False}, {"is_read": True, "auto_cleared": {"$ne": True}}]}, page=page, page_size=page_size,
        )

    async def prune_read(self, user_id: str, keep: int) -> int:
        """Delete every read alert beyond the `keep` newest (and the ones the system cleared)."""
        recent = await self._newest_read_ids(user_id, keep)
        res = await self.collection.delete_many({"user_id": user_id, "is_read": True, "_id": {"$nin": recent}})
        return res.deleted_count
