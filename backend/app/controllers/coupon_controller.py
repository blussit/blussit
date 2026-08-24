from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.coupon_schema import CouponCreateRequest, CouponUpdateRequest, CouponValidateRequest
from app.services.audit_service import AuditService
from app.services.coupon_service import CouponService


class CouponController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = CouponService(db)
        self.audit = AuditService(db)

    async def list(self, pagination: PaginationParams, active_only: bool):
        items, total = await self.service.list_all(pagination.page, pagination.page_size, active_only)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def create(self, current_user: CurrentUser, payload: CouponCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_COUPON", "coupons", result["id"])
        return success(result, "Coupon created successfully")

    async def update(self, current_user: CurrentUser, coupon_id: str, payload: CouponUpdateRequest):
        result = await self.service.update(coupon_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_COUPON", "coupons", coupon_id)
        return success(result, "Coupon updated successfully")

    async def delete(self, current_user: CurrentUser, coupon_id: str):
        await self.service.delete(coupon_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_COUPON", "coupons", coupon_id)
        return success(None, "Coupon deleted successfully")

    async def validate(self, current_user: CurrentUser, payload: CouponValidateRequest):
        coupon, discount = await self.service.validate_and_compute_discount(payload.code, payload.order_value, current_user.id)
        return success({"valid": True, "discount_amount": discount, "coupon_code": coupon["code"]}, "Coupon is valid")
