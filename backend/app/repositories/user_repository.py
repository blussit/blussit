from typing import Optional

from app.repositories.base_repository import BaseRepository, build_search_filter


class UserRepository(BaseRepository):
    collection_name = "users"

    async def find_by_email(self, email: str) -> Optional[dict]:
        return await self.find_one({"email": email})

    async def find_by_phone(self, phone: str) -> Optional[dict]:
        return await self.find_one({"phone": phone})

    async def find_by_identifier(self, identifier: str) -> Optional[dict]:
        return await self.find_one({"$or": [{"email": identifier}, {"phone": identifier}]})

    async def list_by_role(self, role: str, page: int, page_size: int, search: Optional[str] = None, extra_filters: Optional[dict] = None):
        filters: dict = {"role": role}
        if extra_filters:
            filters.update(extra_filters)
        if search:
            filters.update(build_search_filter(search, ["full_name", "email", "phone"]))
        return await self.find_many(filters, page=page, page_size=page_size)
