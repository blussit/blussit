from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.models.enums import BookingStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.review_repository import ReviewRepository
from app.schemas.review_schema import ReviewCreateRequest
from app.utils.serializers import serialize_doc, serialize_list


class ReviewService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = ReviewRepository(db)
        self.booking_repo = BookingRepository(db)

    async def create(self, customer_id: str, payload: ReviewCreateRequest) -> dict:
        booking = await self.booking_repo.find_by_id(payload.booking_id)
        if not booking or booking["customer_id"] != customer_id:
            raise NotFoundException("Booking not found")
        if booking["status"] != BookingStatus.COMPLETED.value:
            raise BadRequestException("You can only review a completed booking")
        if booking.get("is_rated"):
            raise BadRequestException("This booking has already been reviewed")

        doc = payload.model_dump()
        doc["customer_id"] = customer_id
        doc["captain_id"] = booking.get("captain_id")
        doc["service_center_id"] = booking.get("service_center_id")
        created = await self.repo.create(doc)
        await self.booking_repo.update_by_id(payload.booking_id, {"is_rated": True})
        return serialize_doc(created)

    async def list_public(self, page: int, page_size: int):
        items, total = await self.repo.list_public(page, page_size)
        return serialize_list(items), total

    async def captain_rating_summary(self, captain_id: str) -> dict:
        return await self.repo.average_rating_for_captain(captain_id)

    async def list_for_captain(self, captain_id: str) -> list[dict]:
        reviews = await self.repo.list_for_captain(captain_id)
        return serialize_list(reviews)
