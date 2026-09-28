from app.repositories.base_repository import BaseRepository


class AddressRepository(BaseRepository):
    collection_name = "addresses"

    # A customer's saved addresses: the default first, then newest — capped, so
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
