from app.repositories.base_repository import BaseRepository


class FaqRepository(BaseRepository):
    collection_name = "faqs"


class ContactMessageRepository(BaseRepository):
    collection_name = "contact_messages"


class TestimonialRepository(BaseRepository):
    collection_name = "testimonials"


class SettingRepository(BaseRepository):
    collection_name = "settings"

    async def get_by_key(self, key: str) -> dict | None:
        return await self.find_one({"key": key})

    async def upsert(self, key: str, value: dict, description: str | None) -> dict:
        existing = await self.get_by_key(key)
        if existing:
            return await self.update_by_id(str(existing["_id"]), {"value": value, "description": description})
        return await self.create({"key": key, "value": value, "description": description})
