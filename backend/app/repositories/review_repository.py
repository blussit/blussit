from app.repositories.base_repository import BaseRepository


class ReviewRepository(BaseRepository):
    collection_name = "reviews"

    async def list_public(self, page: int, page_size: int):
        return await self.find_many({"is_published": True}, page=page, page_size=page_size)

    async def list_for_captain(self, captain_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"captain_id": captain_id})

    async def average_rating_for_captain(self, captain_id: str) -> float:
        pipeline = [
            {"$match": {"captain_id": captain_id, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}, "count": {"$sum": 1}}},
        ]
        result = await self.aggregate(pipeline)
        return result[0] if result else {"avg_rating": 0, "count": 0}
