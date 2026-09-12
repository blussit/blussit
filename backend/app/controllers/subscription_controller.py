from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.subscription_schema import (
    AssignSubscriptionRequest,
    AutoPayRequest,
    PassQuoteRequest,
    PlanEnquiryRequest,
    SubscribeRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    UpgradeSubscriptionRequest,
)
from app.services.audit_service import AuditService
from app.services.plan_enquiry_service import PlanEnquiryService
from app.services.purchase_confirmation_service import PurchaseConfirmationService
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
        self.confirmations = PurchaseConfirmationService(db)
        self.enquiries = PlanEnquiryService(db)

    async def quote_pass(self, current_user: CurrentUser, payload: PassQuoteRequest):
        return success(
            await self.service.quote_pass(current_user.id, payload.plan_id, payload.vehicle_id, payload.service_id)
        )

    async def submit_enquiry(self, current_user: CurrentUser | None, payload: PlanEnquiryRequest):
        await self.enquiries.capture(
            name=payload.name,
            phone=payload.phone,
            vehicle_count=payload.vehicle_count,
            services_wanted=payload.services_wanted,
            washes_per_month=payload.washes_per_month,
            preferred_time=payload.preferred_time,
            notes=payload.notes,
            customer_id=current_user.id if current_user else None,
        )
        return success(None, "Thanks — we'll call you about a plan that fits.")

    async def list_enquiries(self, pagination: PaginationParams):
        items, total = await self.enquiries.list_for_admin(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def set_enquiry_status(self, current_user: CurrentUser, enquiry_id: str, status: str):
        from app.core.exceptions import NotFoundException

        if not await self.enquiries.set_status(enquiry_id, status):
            raise NotFoundException("Enquiry not found")
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_PLAN_ENQUIRY", "plan_enquiries", enquiry_id, {"status": status}
        )
        return success(None, "Updated")

    async def list_mine(self, current_user: CurrentUser):
        return success(await self.service.list_my_subscriptions(current_user.id))

    async def list_for_customer(self, customer_id: str):
        return success(await self.service.list_for_customer(customer_id))

    async def center_overview(self, current_user: CurrentUser, service_center_id: str):
        return success(await self.service.center_overview(service_center_id, current_user.role, current_user.service_center_id))

    async def subscribe(self, current_user: CurrentUser, payload: SubscribeRequest):
        result = await self.service.subscribe(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "SUBSCRIBE", "user_subscriptions", result["id"], {"plan_id": payload.plan_id})
        result["confirmation_token"] = await self.confirmations.issue(
            "subscription", result["id"], current_user.id, {"plan_name": result.get("plan_name")}
        )
        return success(result, "Subscribed successfully")

    async def assign(self, current_user: CurrentUser, payload: AssignSubscriptionRequest):
        result = await self.service.assign(payload)
        await self.audit.log_action(
            current_user.id, current_user.role, "ASSIGN_SUBSCRIPTION", "user_subscriptions", result["id"], {"customer_id": payload.customer_id}
        )
        return success(result, "Subscription assigned successfully")

    async def cancel(self, current_user: CurrentUser, subscription_id: str):
        result = await self.service.cancel(current_user.id, subscription_id)
        await self.audit.log_action(current_user.id, current_user.role, "CANCEL_SUBSCRIPTION", "user_subscriptions", subscription_id, None)
        return success(result, "Subscription cancelled")

    async def set_auto_pay(self, current_user: CurrentUser, subscription_id: str, payload: AutoPayRequest):
        result = await self.service.set_auto_pay(current_user.id, subscription_id, payload.enabled)
        await self.audit.log_action(
            current_user.id, current_user.role, "SET_SUBSCRIPTION_AUTO_PAY", "user_subscriptions", subscription_id,
            {"enabled": payload.enabled},
        )
        return success(result, "Auto-pay turned on" if payload.enabled else "Auto-pay turned off")

    async def upgrade(self, current_user: CurrentUser, subscription_id: str, payload: UpgradeSubscriptionRequest):
        result = await self.service.upgrade(current_user.id, subscription_id, payload.new_plan_id)
        await self.audit.log_action(
            current_user.id, current_user.role, "UPGRADE_SUBSCRIPTION", "user_subscriptions", subscription_id, {"new_plan_id": payload.new_plan_id}
        )
        return success(result, "Subscription upgraded")

    async def list_all_for_admin(self, pagination: PaginationParams):
        items, total = await self.service.list_all_for_admin(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)
