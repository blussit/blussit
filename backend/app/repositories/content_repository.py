from datetime import datetime, timezone

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

    async def upsert(self, key: str, value: dict, description: str | None, updated_by: str | None = None) -> dict:
        """Writes the setting AND a row in `settings_history` saying what
        changed, from what, by whom — so a price or rule that moved last
        month can be traced instead of argued about. Unchanged writes
        (the admin pressed Save with nothing new) leave no history row."""
        existing = await self.get_by_key(key)
        before = dict((existing or {}).get("value") or {})
        changes = {
            field: {"from": before.get(field), "to": value.get(field)}
            for field in set(before) | set(value)
            if before.get(field) != value.get(field)
        }
        if changes:
            await self.db["settings_history"].insert_one(
                {
                    "key": key,
                    "changes": changes,
                    "changed_by": updated_by,
                    "changed_at": datetime.now(timezone.utc),
                }
            )
        fields = {"value": value, "description": description, "updated_by": updated_by}
        if existing:
            return await self.update_by_id(str(existing["_id"]), fields)
        return await self.create({"key": key, **fields})

    async def history(self, key: str, limit: int = 30) -> list[dict]:
        return await self.db["settings_history"].find({"key": key}).sort("changed_at", -1).to_list(length=limit)
