from datetime import datetime, timezone

from app.repositories.base_repository import BaseRepository


class InventoryRepository(BaseRepository):
    collection_name = "inventory"

    async def list_for_center(self, service_center_id: str, page: int, page_size: int, low_stock_only: bool = False):
        filters: dict = {"service_center_id": service_center_id}
        items, total = await self.find_many(filters, page=page, page_size=page_size)
        if low_stock_only:
            items = [i for i in items if i["quantity_available"] <= i.get("reorder_level", 0)]
            total = len(items)
        return items, total

    async def adjust_quantity(self, item_id: str, delta: float) -> dict | None:
        update: dict = {"$inc": {"quantity_available": delta}, "$set": {"updated_at": datetime.now(timezone.utc)}}
        if delta > 0:
            update["$set"]["last_restocked_at"] = datetime.now(timezone.utc)
        await self.collection.update_one({"_id": self._oid(item_id)}, update)
        return await self.find_by_id(item_id)
