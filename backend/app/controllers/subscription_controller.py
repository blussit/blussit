from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.subscription_schema import (
    AssignSubscriptionRequest,
    SubscribeRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    UpgradeSubscriptionRequest,
)
from app.services.audit_service import AuditService
from app.services.subscription_service import SubscriptionPlanService, UserSubscriptionService


class SubscriptionPlanController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = SubscriptionPlanService(db)
        self.audit = AuditService(db)

    async def list(self, active_only: bool):
        return success(await self.service.list_all(active_only))

    async def get(self, plan_id: str):
        return success(await self.service.get(plan_id))

    async def create(self, current_user: CurrentUser, payload: SubscriptionPlanCreateRequest):
        result = await self.service.create(payload)
        await self.audit.log_action(current_user.id, current_user.role, "CREATE_PLAN", "subscription_plans", result["id"])
        return success(result, "Subscription plan created successfully")

    async def update(self, current_user: CurrentUser, plan_id: str, payload: SubscriptionPlanUpdateRequest):
        result = await self.service.update(plan_id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "UPDATE_PLAN", "subscription_plans", plan_id)
        return success(result, "Subscription plan updated successfully")

    async def delete(self, current_user: CurrentUser, plan_id: str):
        await self.service.delete(plan_id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_PLAN", "subscription_plans", plan_id)
        return success(None, "Subscription plan deleted successfully")


class UserSubscriptionController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = UserSubscriptionService(db)
        self.audit = AuditService(db)

    async def list_mine(self, current_user: CurrentUser):
        return success(await self.service.list_my_subscriptions(current_user.id))

    async def list_for_customer(self, customer_id: str):
        return success(await self.service.list_for_customer(customer_id))

    async def subscribe(self, current_user: CurrentUser, payload: SubscribeRequest):
        result = await self.service.subscribe(current_user.id, payload)
        return success(result, "Subscribed successfully")

    async def assign(self, current_user: CurrentUser, payload: AssignSubscriptionRequest):
        result = await self.service.assign(payload)
        await self.audit.log_action(
            current_user.id, current_user.role, "ASSIGN_SUBSCRIPTION", "user_subscriptions", result["id"], {"customer_id": payload.customer_id}
        )
        return success(result, "Subscription assigned successfully")

    async def cancel(self, current_user: CurrentUser, subscription_id: str):
        result = await self.service.cancel(current_user.id, subscription_id)
        return success(result, "Subscription cancelled")

    async def upgrade(self, current_user: CurrentUser, subscription_id: str, payload: UpgradeSubscriptionRequest):
        result = await self.service.upgrade(current_user.id, subscription_id, payload.new_plan_id)
        return success(result, "Subscription upgraded")

    async def list_all_for_admin(self, pagination: PaginationParams):
        items, total = await self.service.list_all_for_admin(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)
