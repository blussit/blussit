from app.repositories.base_repository import BaseRepository


class ComplaintRepository(BaseRepository):
    collection_name = "complaints"

    async def list_for_customer(self, customer_id: str, page: int, page_size: int):
        return await self.find_many({"customer_id": customer_id}, page=page, page_size=page_size)

    async def list_for_center(self, service_center_id: str, status: str | None, page: int, page_size: int):
        filters: dict = {"service_center_id": service_center_id}
        if status:
            filters["status"] = status
        return await self.find_many(filters, page=page, page_size=page_size)

    async def list_all(self, filters: dict, page: int, page_size: int):
        return await self.find_many(filters, page=page, page_size=page_size)
