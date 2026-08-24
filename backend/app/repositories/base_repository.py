"""
Generic repository implementing the common data-access patterns shared
by every collection: create, find one/many, update, soft delete, count,
and a reusable paginated list with filtering/sorting/search. Concrete
repositories subclass this and add collection-specific queries.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClientSession, AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ReturnDocument


class BaseRepository:
    collection_name: str = ""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection: AsyncIOMotorCollection = db[self.collection_name]

    @staticmethod
    def _oid(value: str) -> ObjectId:
        return ObjectId(value)

    async def create(self, data: dict) -> dict:
        now = datetime.now(timezone.utc)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("is_deleted", False)
        result = await self.collection.insert_one(data)
        return await self.collection.find_one({"_id": result.inserted_id})

    async def find_by_id(
        self, id: str, include_deleted: bool = False, session: Optional[AsyncIOMotorClientSession] = None
    ) -> Optional[dict]:
        if not ObjectId.is_valid(id):
            return None
        query: dict[str, Any] = {"_id": self._oid(id)}
        if not include_deleted:
            query["is_deleted"] = {"$ne": True}
        return await self.collection.find_one(query, session=session)

    async def find_one(self, filters: dict, include_deleted: bool = False) -> Optional[dict]:
        query = dict(filters)
        if not include_deleted:
            query["is_deleted"] = {"$ne": True}
        return await self.collection.find_one(query)

    async def find_many(
        self,
        filters: Optional[dict] = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: int = -1,
        include_deleted: bool = False,
    ) -> tuple[list[dict], int]:
        query = dict(filters) if filters else {}
        if not include_deleted:
            query["is_deleted"] = {"$ne": True}

        total = await self.collection.count_documents(query)
        skip = max(page - 1, 0) * page_size
        cursor = self.collection.find(query).sort(sort_by, sort_order).skip(skip).limit(page_size)
        items = await cursor.to_list(length=page_size)
        return items, total

    async def find_by_ids(self, ids: list[str]) -> list[dict]:
        """Batch lookup — for denormalizing a handful of fields onto a list of
        results (e.g. resolving customer/vehicle/service names onto a
        booking list) without an N+1 query per row."""
        valid_ids = [self._oid(i) for i in set(ids) if i and ObjectId.is_valid(i)]
        if not valid_ids:
            return []
        cursor = self.collection.find({"_id": {"$in": valid_ids}, "is_deleted": {"$ne": True}})
        return await cursor.to_list(length=None)

    async def find_all_no_paginate(
        self,
        filters: Optional[dict] = None,
        sort_by: str = "created_at",
        sort_order: int = -1,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> list[dict]:
        query = dict(filters) if filters else {}
        query.setdefault("is_deleted", {"$ne": True})
        cursor = self.collection.find(query, session=session).sort(sort_by, sort_order)
        return await cursor.to_list(length=None)

    async def update_by_id(self, id: str, data: dict, session: Optional[AsyncIOMotorClientSession] = None) -> Optional[dict]:
        if not ObjectId.is_valid(id):
            return None
        data["updated_at"] = datetime.now(timezone.utc)
        await self.collection.update_one({"_id": self._oid(id)}, {"$set": data}, session=session)
        return await self.find_by_id(id, session=session)

    async def update_if(self, id: str, guard_filter: dict, data: dict) -> Optional[dict]:
        """Same as update_by_id, but the write only takes effect if
        guard_filter still matches the document AT THE MOMENT of the
        update — atomically, in one round trip. Returns None if it didn't
        match (someone else's concurrent write already changed the field
        being guarded). Use this instead of a separate read-then-write
        check anywhere two requests could race on the same document (e.g.
        "only settle this booking's wallet payout once")."""
        if not ObjectId.is_valid(id):
            return None
        data["updated_at"] = datetime.now(timezone.utc)
        query = {"_id": self._oid(id), **guard_filter}
        return await self.collection.find_one_and_update(query, {"$set": data}, return_document=ReturnDocument.AFTER)

    async def soft_delete(self, id: str, deleted_by: Optional[str] = None) -> bool:
        if not ObjectId.is_valid(id):
            return False
        result = await self.collection.update_one(
            {"_id": self._oid(id)},
            {
                "$set": {
                    "is_deleted": True,
                    "deleted_at": datetime.now(timezone.utc),
                    "updated_by": deleted_by,
                }
            },
        )
        return result.modified_count > 0

    async def hard_delete(self, id: str) -> bool:
        if not ObjectId.is_valid(id):
            return False
        result = await self.collection.delete_one({"_id": self._oid(id)})
        return result.deleted_count > 0

    async def count(self, filters: Optional[dict] = None, include_deleted: bool = False) -> int:
        query = dict(filters) if filters else {}
        if not include_deleted:
            query["is_deleted"] = {"$ne": True}
        return await self.collection.count_documents(query)

    async def aggregate(self, pipeline: list[dict]) -> list[dict]:
        cursor = self.collection.aggregate(pipeline)
        return await cursor.to_list(length=None)


def build_search_filter(search: Optional[str], fields: list[str]) -> dict:
    """Builds a case-insensitive regex $or filter across multiple text fields."""
    if not search:
        return {}
    return {"$or": [{field: {"$regex": search, "$options": "i"}} for field in fields]}
