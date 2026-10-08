from typing import Optional

from app.repositories.base_repository import BaseRepository, build_search_filter


class UserRepository(BaseRepository):
    collection_name = "users"

    async def find_by_email(self, email: str) -> Optional[dict]:
        # Emails are stored lower-case; the raw form still matches an old
        # mixed-case row until lowercase_user_emails has run.
        raw = (email or "").strip()
        return await self.find_one({"email": {"$in": list({raw, raw.lower()})}})

    async def find_by_phone(self, phone: str) -> Optional[dict]:
        return await self.find_one({"phone": phone})

    async def find_by_identifier(self, identifier: str) -> Optional[dict]:
        raw = (identifier or "").strip()
        if "@" in raw:
            return await self.find_by_email(raw)
        return await self.find_one({"$or": [{"email": raw}, {"phone": raw}]})


async def lowercase_user_emails(db) -> int:
    """Boot task: store every account email lower-case so login is
    case-insensitive. Idempotent; an address whose lower-case form already
    belongs to another account is left as is and logged (an admin decides)."""
    import logging

    log = logging.getLogger(__name__)
    changed = 0
    async for user in db.users.find({"email": {"$regex": "[A-Z]"}}, {"email": 1}):
        lower = user["email"].strip().lower()
        if await db.users.find_one({"email": lower, "_id": {"$ne": user["_id"]}}, {"_id": 1}):
            log.warning("Email not lower-cased: %s already used by another account (user %s)", lower.split("@")[-1], user["_id"])
            continue
        res = await db.users.update_one({"_id": user["_id"], "email": user["email"]}, {"$set": {"email": lower}})
        changed += res.modified_count
    return changed

    async def list_by_role(self, role: str, page: int, page_size: int, search: Optional[str] = None, extra_filters: Optional[dict] = None):
        filters: dict = {"role": role}
        if extra_filters:
            filters.update(extra_filters)
        if search:
            filters.update(build_search_filter(search, ["full_name", "email", "phone"]))
        return await self.find_many(filters, page=page, page_size=page_size)
