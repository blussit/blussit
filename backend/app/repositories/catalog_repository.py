from app.repositories.base_repository import BaseRepository, build_search_filter


class CategoryRepository(BaseRepository):
    collection_name = "categories"


class ComboOfferRepository(BaseRepository):
    collection_name = "combo_offers"

    async def list_active(self) -> list[dict]:
        return await self.find_all_no_paginate({"is_active": True}, sort_by="display_order", sort_order=1)


class ServiceRepository(BaseRepository):
    collection_name = "services"

    async def list_by_category(self, category_id: str) -> list[dict]:
        return await self.find_all_no_paginate({"category_id": category_id, "is_active": True})

    async def search_services(self, search: str | None, category_id: str | None, vehicle_type: str | None, page: int, page_size: int, active_only: bool = True):
        filters: dict = {}
        if active_only:
            filters["is_active"] = True
        if category_id:
            filters["category_id"] = category_id
        if vehicle_type:
            filters["vehicle_types"] = vehicle_type
        if search:
            filters.update(build_search_filter(search, ["name", "description"]))
        return await self.find_many(filters, page=page, page_size=page_size, sort_by="display_order", sort_order=1)
