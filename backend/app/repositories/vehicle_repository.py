from app.repositories.base_repository import BaseRepository


class VehicleRepository(BaseRepository):
    collection_name = "vehicles"

    async def list_by_owner(self, owner_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"owner_id": owner_id})

    async def clear_default(self, owner_id: str) -> None:
        await self.collection.update_many({"owner_id": owner_id}, {"$set": {"is_default": False}})

    async def distinct_owners_for_registration(self, normalized: str, exclude_owner_id: str | None = None) -> list[str]:
        """Every DISTINCT owner_id currently holding this normalized plate
        (excluding one owner if given, e.g. the customer doing the
        checking) — the semi-unique rule counts accounts, not vehicle
        rows, so an owner who somehow has the same plate saved twice
        still only counts once."""
        query: dict = {"registration_number_normalized": normalized, "is_deleted": {"$ne": True}}
        if exclude_owner_id:
            query["owner_id"] = {"$ne": exclude_owner_id}
        owner_ids = await self.collection.distinct("owner_id", query)
        return owner_ids
