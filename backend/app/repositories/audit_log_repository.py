from app.repositories.base_repository import BaseRepository


class AuditLogRepository(BaseRepository):
    collection_name = "audit_logs"

    async def list_all(self, filters: dict, page: int, page_size: int):
        return await self.find_many(filters, page=page, page_size=page_size, include_deleted=True)
