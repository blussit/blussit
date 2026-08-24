from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.review_schema import ReviewCreateRequest
from app.services.review_service import ReviewService


class ReviewController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ReviewService(db)

    async def create(self, current_user: CurrentUser, payload: ReviewCreateRequest):
        result = await self.service.create(current_user.id, payload)
        return success(result, "Thank you for your feedback")

    async def list_public(self, pagination: PaginationParams):
        items, total = await self.service.list_public(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def captain_summary(self, captain_id: str):
        return success(await self.service.captain_rating_summary(captain_id))

    async def list_for_captain(self, captain_id: str):
        return success(await self.service.list_for_captain(captain_id))
