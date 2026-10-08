from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ValidationError

from app.core.exceptions import BadRequestException, NotFoundException
from app.repositories.content_repository import ContactMessageRepository, FaqRepository, SettingRepository, TestimonialRepository
from app.schemas.content_schema import (
    ContactMessageCreateRequest,
    FaqCreateRequest,
    FaqUpdateRequest,
    SettingUpsertRequest,
    TestimonialCreateRequest,
    TestimonialUpdateRequest,
)
from app.utils.serializers import serialize_doc, serialize_list


class FaqService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = FaqRepository(db)

    async def list_all(self, active_only: bool = False):
        filters = {"is_active": True} if active_only else None
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def create(self, payload: FaqCreateRequest) -> dict:
        return serialize_doc(await self.repo.create(payload.model_dump()))

    async def update(self, faq_id: str, payload: FaqUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(faq_id, data)
        if not updated:
            raise NotFoundException("FAQ not found")
        return serialize_doc(updated)

    async def delete(self, faq_id: str) -> None:
        if not await self.repo.soft_delete(faq_id):
            raise NotFoundException("FAQ not found")


class TestimonialService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = TestimonialRepository(db)

    async def list_all(self, featured_only: bool = False):
        filters = {"is_featured": True} if featured_only else None
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def create(self, payload: TestimonialCreateRequest) -> dict:
        return serialize_doc(await self.repo.create(payload.model_dump()))

    async def update(self, testimonial_id: str, payload: TestimonialUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        updated = await self.repo.update_by_id(testimonial_id, data)
        if not updated:
            raise NotFoundException("Testimonial not found")
        return serialize_doc(updated)

    async def delete(self, testimonial_id: str) -> None:
        if not await self.repo.soft_delete(testimonial_id):
            raise NotFoundException("Testimonial not found")


# Settings with their own validated, audited endpoint. The generic PUT
# /settings never writes them (ADM-01): it used to upsert ANY key unchecked —
# a bad booking_policy value took booking down, and pricing_config
# {"per_km_rate": "abc"} made every booking 500.
DEDICATED_SETTING_ENDPOINTS = {
    "booking_policy": "PUT /booking-policy",
    "pricing_config": "PUT /pricing-config",
    "homepage_config": "PUT /homepage-config",
}

# The ONLY keys PUT /settings may write, each with the schema its value must
# satisfy (use extra="forbid"). Empty on purpose: every setting the app
# reads has a dedicated endpoint above, and no admin screen calls PUT
# /settings. A future key that nothing else edits gets a strict schema here.
GENERIC_SETTINGS: dict[str, type[BaseModel]] = {}


class SettingService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = SettingRepository(db)

    @staticmethod
    def validated_value(key: str, value: dict) -> dict:
        """Fail closed: a dedicated key is sent to its endpoint, an unknown
        key is refused, a whitelisted key's value must pass its schema."""
        if key in DEDICATED_SETTING_ENDPOINTS:
            raise BadRequestException(
                f"'{key}' can't be changed here — use the dedicated endpoint ({DEDICATED_SETTING_ENDPOINTS[key]}), which validates it."
            )
        schema = GENERIC_SETTINGS.get(key)
        if schema is None:
            raise BadRequestException(f"'{key}' isn't a setting that can be changed here.")
        try:
            return schema.model_validate(value).model_dump()
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise BadRequestException(f"Invalid value for '{key}'{f' ({where})' if where else ''}: {first.get('msg', 'not allowed')}.") from exc

    async def get(self, key: str) -> dict:
        setting = await self.repo.get_by_key(key)
        if not setting:
            raise NotFoundException("Setting not found")
        return serialize_doc(setting)

    async def upsert(self, payload: SettingUpsertRequest, updated_by: str | None = None) -> tuple[dict, dict]:
        """(saved setting, {field: {before, after}}) — the caller audits the
        changes. Only whitelisted keys with a valid value get this far."""
        value = self.validated_value(payload.key, payload.value)
        existing = await self.repo.get_by_key(payload.key)
        from app.services.audit_service import field_changes

        changes = field_changes((existing or {}).get("value") or {}, value)
        result = await self.repo.upsert(payload.key, value, payload.description, updated_by=updated_by)
        return serialize_doc(result), changes

    async def list_all(self):
        items = await self.repo.find_all_no_paginate()
        return serialize_list(items)


class ContactMessageService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ContactMessageRepository(db)

    async def create(self, payload: ContactMessageCreateRequest) -> dict:
        created = await self.repo.create(payload.model_dump())
        return serialize_doc(created)

    async def list_all(self, page: int, page_size: int):
        items, total = await self.repo.find_many(page=page, page_size=page_size)
        return serialize_list(items), total
