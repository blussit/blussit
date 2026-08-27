from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.review_schema import ReviewCreateRequest, ReviewUpdateRequest
from app.services.audit_service import AuditService
from app.services.review_service import ReviewService


class ReviewController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = ReviewService(db)
        self.audit = AuditService(db)

    async def create(self, current_user: CurrentUser, payload: ReviewCreateRequest):
        result = await self.service.create(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_REVIEW", "reviews", result["id"], {"booking_id": payload.booking_id})
        return success(result, "Thank you for your feedback")

    async def update(self, current_user: CurrentUser, review_id: str, payload: ReviewUpdateRequest):
        result = await self.service.update(current_user.id, review_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_REVIEW", "reviews", review_id, payload.model_dump(exclude_unset=True))
        return success(result, "Review updated")

    async def delete(self, current_user: CurrentUser, review_id: str):
        await self.service.delete(current_user.id, review_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_REVIEW", "reviews", review_id, None)
        return success(None, "Review deleted")

    async def list_my_reviews(self, current_user: CurrentUser):
        return success(await self.service.list_my_reviews(current_user.id))

    async def list_public(self, pagination: PaginationParams):
        items, total = await self.service.list_public(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_all_for_admin(self, pagination: PaginationParams, include_deleted: bool):
        items, total = await self.service.list_all_for_admin(pagination.page, pagination.page_size, include_deleted)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def captain_summary(self, captain_id: str):
        return success(await self.service.captain_rating_summary(captain_id))

    async def list_for_captain(self, captain_id: str):
        return success(await self.service.list_for_captain(captain_id))

    async def get_for_booking(self, current_user: CurrentUser, booking_id: str):
        return success(await self.service.get_for_booking(booking_id, current_user.id, current_user.role))

    async def list_for_center(self, current_user: CurrentUser, service_center_id: str, pagination: PaginationParams):
        items, total = await self.service.list_for_center(service_center_id, current_user.role, current_user.service_center_id, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)
