from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.repositories.booking_repository import BookingRepository
from app.repositories.content_repository import ContactMessageRepository, FaqRepository, SettingRepository, TestimonialRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.user_repository import UserRepository
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


class SettingService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = SettingRepository(db)

    async def get(self, key: str) -> dict:
        setting = await self.repo.get_by_key(key)
        if not setting:
            raise NotFoundException("Setting not found")
        return serialize_doc(setting)

    async def upsert(self, payload: SettingUpsertRequest) -> dict:
        result = await self.repo.upsert(payload.key, payload.value, payload.description)
        return serialize_doc(result)

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


class PublicStatsService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.booking_repo = BookingRepository(db)
        self.user_repo = UserRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.review_repo = ReviewRepository(db)

    async def landing_page_stats(self) -> dict:
        vehicles_serviced = await self.booking_repo.count({"status": "completed"})
        happy_customers = await self.user_repo.count({"role": "customer"})
        service_centers = await self.center_repo.count({"is_active": True})

        pipeline = [{"$match": {"is_deleted": {"$ne": True}}}, {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}}}]
        rating_result = await self.review_repo.aggregate(pipeline)
        average_rating = round(rating_result[0]["avg_rating"], 1) if rating_result and rating_result[0].get("avg_rating") else 4.8

        return {
            "vehicles_serviced": vehicles_serviced,
            "happy_customers": happy_customers,
            "service_centers": service_centers,
            "average_rating": average_rating,
        }
