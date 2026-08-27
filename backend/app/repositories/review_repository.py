from app.repositories.base_repository import BaseRepository


class ReviewRepository(BaseRepository):
    collection_name = "reviews"

    async def list_public(self, page: int, page_size: int):
        return await self.find_many({"is_published": True}, page=page, page_size=page_size)

    async def list_for_captain(self, captain_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"captain_id": captain_id})

    async def list_for_customer(self, customer_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"customer_id": customer_id})

    async def find_by_booking_id(self, booking_id: str) -> dict | None:
        return await self.find_one({"booking_id": booking_id})

    async def list_all_for_admin(self, page: int, page_size: int, include_deleted: bool = False):
        return await self.find_many({}, page=page, page_size=page_size, include_deleted=include_deleted)

    async def average_rating_for_captain(self, captain_id: str) -> float:
        # $ifNull prefers the new captain_rating field, falling back to the
        # legacy flat `rating` for reviews written before the split existed
        # — so a captain's average stays correct across both shapes.
        pipeline = [
            {"$match": {"captain_id": captain_id, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "avg_rating": {"$avg": {"$ifNull": ["$captain_rating", "$rating"]}}, "count": {"$sum": 1}}},
        ]
        result = await self.aggregate(pipeline)
        return result[0] if result else {"avg_rating": 0, "count": 0}
