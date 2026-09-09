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

    async def create(self, data: dict, session: Optional[AsyncIOMotorClientSession] = None) -> dict:
        now = datetime.now(timezone.utc)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("is_deleted", False)
        result = await self.collection.insert_one(data, session=session)
        return await self.collection.find_one({"_id": result.inserted_id}, session=session)

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
        # Soft-deleted docs are read-only tombstones — every READ helper
        # already excludes them; a write slipping through returned a
        # "successful" update on a document no list would ever show again.
        await self.collection.update_one({"_id": self._oid(id), "is_deleted": {"$ne": True}}, {"$set": data}, session=session)
        return await self.find_by_id(id, session=session)

    async def update_if(self, id: str, guard_filter: dict, data: dict, session: Optional[AsyncIOMotorClientSession] = None) -> Optional[dict]:
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
        query = {"_id": self._oid(id), "is_deleted": {"$ne": True}, **guard_filter}
        return await self.collection.find_one_and_update(query, {"$set": data}, return_document=ReturnDocument.AFTER, session=session)

    async def get_or_init(self, filter: dict, defaults: dict, session: Optional[AsyncIOMotorClientSession] = None) -> dict:
        """Idempotent get-or-create: if no document matches `filter`, inserts
        one with filter + defaults merged; if one already exists, returns it
        untouched. Uses $setOnInsert so concurrent first-callers racing to
        create the "same" document (e.g. two customers both making the
        first booking attempt for a slot that has no capacity doc yet)
        don't stomp each other — exactly one insert wins the race, every
        other caller's upsert is a no-op that then reads the winner's
        document. Requires a unique index on `filter`'s fields for the
        race-safety to actually hold (Mongo, not this code, resolves the
        race). Use this same path for BOTH lazy first-use and any admin
        edit of the same kind of document, so there is only ever one
        initialization code path, not two that could disagree."""
        now = datetime.now(timezone.utc)
        await self.collection.update_one(
            filter,
            {"$setOnInsert": {**filter, **defaults, "created_at": now, "updated_at": now, "is_deleted": False}},
            upsert=True,
            session=session,
        )
        return await self.collection.find_one(filter, session=session)

    async def increment_if(
        self,
        filter: dict,
        inc: dict[str, int],
        expr_guard: Optional[list] = None,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> Optional[dict]:
        """Atomically increments one or more numeric fields in a single
        round trip, but only if `expr_guard` (a MongoDB $expr comparison
        between two fields on the SAME document, e.g.
        ["$lt", "$booked_count", "$capacity"]) is satisfied at write time.
        Returns the updated document, or None if the guard didn't match
        (e.g. "already at capacity"). Unlike update_if (guards on an
        equality snapshot for read-then-retry patterns), the whole success
        condition here is expressible directly in the filter, so this
        needs no retry loop — for counters like slot capacity where
        "count < capacity" is a live comparison to make right now, not a
        "did anything change since I read" check."""
        query = dict(filter)
        if expr_guard:
            query["$expr"] = {expr_guard[0]: [expr_guard[1], expr_guard[2]]}
        return await self.collection.find_one_and_update(
            query,
            {"$inc": inc, "$set": {"updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )

    async def push_to_array(self, id: str, field: str, item: dict, session: Optional[AsyncIOMotorClientSession] = None) -> Optional[dict]:
        """Appends one item to an array field in a single round trip (e.g. a
        new entry on a complaint's reply thread) — never overwrites the
        array, unlike update_by_id's $set. Append-only history, same spirit
        as increment_if for counters: the whole operation is one atomic
        write, no read-then-write race."""
        if not ObjectId.is_valid(id):
            return None
        await self.collection.update_one(
            {"_id": self._oid(id), "is_deleted": {"$ne": True}},
            {"$push": {field: item}, "$set": {"updated_at": datetime.now(timezone.utc)}},
            session=session,
        )
        return await self.find_by_id(id, session=session)

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
    """Builds a case-insensitive regex $or filter across multiple text
    fields. The search text is escaped — user input must never reach the
    regex engine raw (ReDoS / unintended matches)."""
    if not search:
        return {}
    import re

    escaped = re.escape(search.strip())
    return {"$or": [{field: {"$regex": escaped, "$options": "i"}} for field in fields]}
