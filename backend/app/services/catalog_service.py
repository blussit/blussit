from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import ConflictException, NotFoundException
from app.repositories.catalog_repository import CategoryRepository, ComboOfferRepository, ServiceRepository
from app.schemas.catalog_schema import (
    CategoryCreateRequest,
    CategoryUpdateRequest,
    ComboOfferCreateRequest,
    ComboOfferUpdateRequest,
    ServiceCreateRequest,
    ServiceUpdateRequest,
)
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.text import slugify


class CategoryService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = CategoryRepository(db)

    async def list_all(self, active_only: bool = False) -> list[dict]:
        filters = {"is_active": True} if active_only else None
        items = await self.repo.find_all_no_paginate(filters, sort_by="display_order", sort_order=1)
        return serialize_list(items)

    async def create(self, payload: CategoryCreateRequest) -> dict:
        slug = slugify(payload.name)
        if await self.repo.find_one({"slug": slug}):
            raise ConflictException("A category with this name already exists")
        doc = payload.model_dump()
        doc["slug"] = slug
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, category_id: str, payload: CategoryUpdateRequest) -> dict:
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        updated = await self.repo.update_by_id(category_id, data)
        if not updated:
            raise NotFoundException("Category not found")
        return serialize_doc(updated)

    async def delete(self, category_id: str) -> None:
        deleted = await self.repo.soft_delete(category_id)
        if not deleted:
            raise NotFoundException("Category not found")


# Services saved before these fields existed read back with the defaults.
_SERVICE_DEFAULTS = {"prepaid_only": False, "charges_travel": False, "offer_tag": None}
# Fields where "no value" is a real setting: sent explicitly as null (or a
# blank offer tag) on edit, they are cleared rather than skipped — a stale
# first-time price would otherwise keep undercutting an offer price.
_CLEARABLE_SERVICE_FIELDS = ("offer_tag", "discounted_price", "original_price", "variant_group", "variant_label", "captain_fee")


def serialize_service(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return serialize_doc({**_SERVICE_DEFAULTS, **doc})


class ServiceCatalogService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ServiceRepository(db)
        self.category_repo = CategoryRepository(db)

    async def list_services(self, page: int, page_size: int, search: str | None, category_id: str | None, vehicle_type: str | None, active_only: bool = True):
        items, total = await self.repo.search_services(search, category_id, vehicle_type, page, page_size, active_only)
        return [serialize_service(i) for i in items], total

    async def get(self, service_id: str) -> dict:
        service = await self.repo.find_by_id(service_id)
        if not service:
            raise NotFoundException("Service not found")
        return serialize_service(service)

    async def create(self, payload: ServiceCreateRequest) -> dict:
        category = await self.category_repo.find_by_id(payload.category_id)
        if not category:
            raise NotFoundException("Category not found")
        slug = slugify(payload.name)
        doc = payload.model_dump()
        doc["slug"] = slug
        created = await self.repo.create(doc)
        return serialize_service(created)

    async def update(self, service_id: str, payload: ServiceUpdateRequest) -> dict:
        sent = payload.model_dump(exclude_unset=True)
        data = {k: v for k, v in sent.items() if v is not None or k in _CLEARABLE_SERVICE_FIELDS}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        updated = await self.repo.update_by_id(service_id, data)
        if not updated:
            raise NotFoundException("Service not found")
        return serialize_service(updated)

    async def delete(self, service_id: str) -> None:
        deleted = await self.repo.soft_delete(service_id)
        if not deleted:
            raise NotFoundException("Service not found")


class ComboOfferService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ComboOfferRepository(db)
        self.service_repo = ServiceRepository(db)

    async def list_all(self, active_only: bool = False):
        items = await (self.repo.list_active() if active_only else self.repo.find_all_no_paginate(None, sort_by="display_order", sort_order=1))
        return serialize_list(items)

    async def get(self, combo_id: str) -> dict:
        combo = await self.repo.find_by_id(combo_id)
        if not combo:
            raise NotFoundException("Combo offer not found")
        return serialize_doc(combo)

    async def create(self, payload: ComboOfferCreateRequest) -> dict:
        for service_id in payload.service_ids:
            if not await self.service_repo.find_by_id(service_id):
                raise NotFoundException(f"Service not found: {service_id}")
        doc = payload.model_dump()
        doc["slug"] = slugify(payload.name)
        created = await self.repo.create(doc)
        return serialize_doc(created)

    async def update(self, combo_id: str, payload: ComboOfferUpdateRequest) -> dict:
        # discounted_price / description sent as null = cleared (a removed
        # first-time price used to stay live).
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in ("discounted_price", "description")}
        if "name" in data:
            data["slug"] = slugify(data["name"])
        updated = await self.repo.update_by_id(combo_id, data)
        if not updated:
            raise NotFoundException("Combo offer not found")
        return serialize_doc(updated)

    async def delete(self, combo_id: str) -> None:
        deleted = await self.repo.soft_delete(combo_id)
        if not deleted:
            raise NotFoundException("Combo offer not found")
