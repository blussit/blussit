from app.repositories.base_repository import BaseRepository


class VehicleRepository(BaseRepository):
    collection_name = "vehicles"

    async def list_by_owner(self, owner_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"owner_id": owner_id})

    async def clear_default(self, owner_id: str) -> None:
        await self.collection.update_many({"owner_id": owner_id}, {"$set": {"is_default": False}})
