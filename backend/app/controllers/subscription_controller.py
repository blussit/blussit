from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_customer_in_scope
from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.subscription_schema import (
    AutoPayRequest,
    PassQuoteRequest,
    PlanEnquiryRequest,
    SubscribeRequest,
    SubscriptionExtendRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    UpgradeSubscriptionRequest,
)
from app.services.audit_service import AuditService
from app.services.plan_enquiry_service import ENQUIRY_STATUSES, PlanEnquiryService
from app.services.purchase_confirmation_service import PurchaseConfirmationService
from app.services.subscription_service import SubscriptionPlanService, UserSubscriptionService, extension_message


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

    async def discontinue(self, current_user: CurrentUser, plan_id: str):
        result = await self.service.discontinue(plan_id)
        await self.audit.log_action(current_user.id, current_user.role, "DISCONTINUE_PLAN", "subscription_plans", plan_id)
        return success(result, "Plan discontinued — no longer sold, existing subscribers are unaffected")


class UserSubscriptionController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.service = UserSubscriptionService(db)
        self.audit = AuditService(db)
        self.confirmations = PurchaseConfirmationService(db)
        self.enquiries = PlanEnquiryService(db)

    async def quote_pass(self, current_user: CurrentUser, payload: PassQuoteRequest):
        return success(
            await self.service.quote_pass(current_user.id, payload.plan_id, payload.vehicle_id, payload.service_id, vehicle_type=payload.vehicle_type)
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
        from app.core.exceptions import BadRequestException, NotFoundException

        # A typo'd status used to answer "Enquiry not found" — name the
        # real problem instead.
        if status not in ENQUIRY_STATUSES:
            raise BadRequestException(
                f"Status must be one of: {', '.join(ENQUIRY_STATUSES)} (you sent '{status}').", {"allowed": list(ENQUIRY_STATUSES)},
            )
        if not await self.enquiries.set_status(enquiry_id, status):
            raise NotFoundException("Enquiry not found")
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_PLAN_ENQUIRY", "plan_enquiries", enquiry_id, {"status": status}
        )
        return success({"id": enquiry_id, "status": status}, f"Enquiry marked {status}.")

    async def _plan_name(self, plan_id: str | None) -> str | None:
        from bson import ObjectId

        if not plan_id or not ObjectId.is_valid(plan_id):
            return None
        plan = await self.db.subscription_plans.find_one({"_id": ObjectId(plan_id)}, {"name": 1})
        return (plan or {}).get("name")

    async def list_mine(self, current_user: CurrentUser):
        return success(await self.service.list_my_subscriptions(current_user.id))

    async def list_for_customer(self, customer_id: str, current_user: CurrentUser | None = None):
        if current_user is None:
            return success(await self.service.list_for_customer(customer_id))
        # MGR-04: a manager reads plans only of a customer known to their
        # center (the same rule as the 360 view) — 404 otherwise.
        await ensure_customer_in_scope(self.db, current_user.role, current_user.service_center_id, customer_id)
        return success(await self.service.list_for_customer(customer_id, current_user.role, current_user.service_center_id))

    async def center_overview(self, current_user: CurrentUser, service_center_id: str):
        return success(await self.service.center_overview(service_center_id, current_user.role, current_user.service_center_id))

    async def usage_history(self, subscription_id: str, current_user: CurrentUser | None = None):
        if current_user is None:
            return success(await self.service.usage_history(subscription_id))
        return success(await self.service.usage_history(subscription_id, current_user.role, current_user.service_center_id))

    async def subscribe(self, current_user: CurrentUser, payload: SubscribeRequest):
        result = await self.service.subscribe(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "SUBSCRIBE", "user_subscriptions", result["id"], {"plan_id": payload.plan_id})
        result["confirmation_token"] = await self.confirmations.issue(
            "subscription", result["id"], current_user.id, {"plan_name": result.get("plan_name")}
        )
        return success(result, f"You're subscribed to {result.get('plan_name') or 'the plan'}.")

    async def cancel(self, current_user: CurrentUser, subscription_id: str):
        result = await self.service.cancel(current_user.id, subscription_id)
        await self.audit.log_action(current_user.id, current_user.role, "CANCEL_SUBSCRIPTION", "user_subscriptions", subscription_id, None)
        name = await self._plan_name(result.get("plan_id"))
        return success(
            result,
            f"{name or 'Your plan'} cancelled — it can't be used for new washes any more. "
            "Auto-pay (if it was on) is switched off, so no further payment is taken.",
        )

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
        name = await self._plan_name(payload.new_plan_id)
        retired = " Auto-pay was switched off — turn it on again for the new plan." if result.get("auto_pay_retired") else ""
        return success(result, f"Switched to {name or 'the new plan'}. Washes already used this cycle still count.{retired}")

    async def extend(self, current_user: CurrentUser, subscription_id: str, payload: SubscriptionExtendRequest):
        result = await self.service.extend_pass(
            subscription_id, payload.days, note=payload.note, actor_id=current_user.id, actor_role=current_user.role,
            actor_center_id=current_user.service_center_id,
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "EXTEND_PASS", "user_subscriptions", subscription_id,
            {"days": payload.days, "note": payload.note, "extension_days": result.get("extension_days"),
             "extended_until": result.get("extended_until"), "society_id": result.get("society_id"),
             "custom_plan_id": result.get("custom_plan_id")},
        )
        return success(result, extension_message(payload.days, result))

    async def admin_overview(self):
        return success(await self.service.admin_overview())

    async def plan_purchases(self, s, e, pagination: PaginationParams, service_center_id: str | None = None, extra: dict | None = None):
        items, total = await self.service.plan_purchases(s, e, pagination.page, pagination.page_size, service_center_id, extra)
        return paginated(items, pagination.page, pagination.page_size, total)
