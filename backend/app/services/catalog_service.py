from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.enums import BookingStatus
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


# Every booking state still owed work or money — reference data such a
# booking points at must not disappear under it (ADM-06).
LIVE_BOOKING_STATUSES = [s.value for s in BookingStatus if s not in (BookingStatus.COMPLETED, BookingStatus.CANCELLED)]
# A scheduled pass (a custom-plan renewal waiting for its start) is live too.
_LIVE_PASS_STATUSES = ["active", "paused", "scheduled"]


def _names(rows: list[dict], key: str = "name", limit: int = 6) -> str:
    shown = [str(r.get(key) or r["_id"]) for r in rows[:limit]]
    more = len(rows) - limit
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


async def _live_bookings(db, match: dict) -> list[dict]:
    return await db.bookings.find(
        {**match, "status": {"$in": LIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}, {"booking_number": 1},
    ).to_list(length=50)


def in_use_refusal(what: str, uses: list[str]) -> BadRequestException:
    return BadRequestException(
        f"This {what} is still in use — {'; '.join(uses)}. Switch it off instead (untick Active): it leaves the "
        "site and new bookings, and everything already booked or sold keeps working."
    )


async def service_uses(db, service_id: str) -> list[str]:
    """What still points at this service: live bookings, plans that include
    it (and passes sold on them), passes bought for it, combos, the
    homepage hero. Deleting under any of these broke them (ADM-06)."""
    uses: list[str] = []
    live = await _live_bookings(db, {"service_ids": service_id})
    if live:
        uses.append(f"{len(live)} live booking(s) ({_names(live, 'booking_number')})")
    plans = await db.subscription_plans.find(
        {"included_service_ids": service_id, "is_deleted": {"$ne": True}}, {"name": 1},
    ).to_list(length=50)
    if plans:
        uses.append(f"plan(s) {_names(plans)}")
    passes = await db.user_subscriptions.count_documents({
        "status": {"$in": _LIVE_PASS_STATUSES}, "is_deleted": {"$ne": True},
        "$or": [{"service_id": service_id}, {"plan_id": {"$in": [str(p["_id"]) for p in plans]}}],
    })
    if passes:
        uses.append(f"{passes} active pass(es)")
    combos = await db.combo_offers.find({"service_ids": service_id, "is_deleted": {"$ne": True}}, {"name": 1}).to_list(length=50)
    if combos:
        uses.append(f"combo(s) {_names(combos)}")
    home = await db.settings.find_one({"key": "homepage_config", "value.featured_service_id": service_id}, {"_id": 1})
    if home:
        uses.append("the homepage's featured service")
    return uses


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
        if not await self.repo.find_by_id(category_id):
            raise NotFoundException("Category not found")
        services = await self.repo.db.services.find({"category_id": category_id, "is_deleted": {"$ne": True}}, {"name": 1}).to_list(length=50)
        if services:
            raise in_use_refusal("category", [f"service(s) {_names(services)} — move or delete them first"])
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


# What a captain is paid per job — internal pay, shown only to the
# catalogue's editors (admin/manager), never on the public catalogue.
_INTERNAL_SERVICE_FIELDS = ("captain_fee",)


def public_service(service: dict) -> dict:
    return {k: v for k, v in service.items() if k not in _INTERNAL_SERVICE_FIELDS}


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
        if not ObjectId.is_valid(service_id) or not await self.repo.find_by_id(service_id):
            raise NotFoundException("Service not found")
        uses = await service_uses(self.repo.db, service_id)
        if uses:
            raise in_use_refusal("service", uses)
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
