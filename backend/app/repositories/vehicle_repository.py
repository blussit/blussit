from bson import ObjectId

from app.repositories.base_repository import BaseRepository


class VehicleRepository(BaseRepository):
    collection_name = "vehicles"

    # A customer's saved vehicles: the default first, then newest — capped, so
    # one runaway account can't make every booking screen load thousands.
    LIST_LIMIT = 50

    async def list_by_owner(self, owner_id: str) -> list[dict]:
        cursor = (
            self.collection.find({"owner_id": owner_id, "is_deleted": {"$ne": True}})
            .sort([("is_default", -1), ("created_at", -1)])
            .limit(self.LIST_LIMIT)
        )
        return await cursor.to_list(length=self.LIST_LIMIT)

    async def clear_default(self, owner_id: str) -> None:
        await self.collection.update_many({"owner_id": owner_id}, {"$set": {"is_default": False}})

    async def find_owner_plate(self, owner_id: str, normalized: str, exclude_id: str | None = None) -> dict | None:
        """This owner's live vehicle with this normalized plate, if any
        (indexed: registration_number_normalized)."""
        query: dict = {"owner_id": owner_id, "registration_number_normalized": normalized, "is_deleted": {"$ne": True}}
        if exclude_id and ObjectId.is_valid(exclude_id):
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        return await self.collection.find_one(query, {"_id": 1})

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
