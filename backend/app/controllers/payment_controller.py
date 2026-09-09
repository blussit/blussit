from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.payment_schema import CollectPaymentRequest, CreateOrderRequest, VerifyPaymentRequest
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService
from app.services.purchase_confirmation_service import PurchaseConfirmationService


class PaymentController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = PaymentService(db)
        self.audit = AuditService(db)
        self.confirmations = PurchaseConfirmationService(db)

    async def create_order(self, current_user: CurrentUser, payload: CreateOrderRequest):
        return success(await self.service.create_order(current_user.id, payload))

    async def collect_cash(self, current_user: CurrentUser, payload: CollectPaymentRequest):
        result = await self.service.captain_collect_cash(payload.booking_id, current_user.id)
        await self.audit.log_action(
            current_user.id, current_user.role, "CASH_COLLECTED", "bookings", payload.booking_id, None
        )
        return success(result, "Cash collection recorded")

    async def collect_link(self, current_user: CurrentUser, payload: CollectPaymentRequest):
        return success(await self.service.captain_payment_link(payload.booking_id, current_user.id))

    async def collect_status(self, current_user: CurrentUser, booking_id: str):
        return success(await self.service.captain_check_payment(booking_id, current_user.id))

    async def center_collections(self, current_user: CurrentUser, service_center_id: str, date_from: str | None, date_to: str | None):
        return success(
            await self.service.center_collections(service_center_id, current_user.role, current_user.service_center_id, date_from, date_to)
        )

    async def admin_collections(self, date_from: str | None, date_to: str | None):
        return success(await self.service.admin_collections(date_from, date_to))

    async def verify(self, current_user: CurrentUser, payload: VerifyPaymentRequest):
        result = await self.service.verify_payment(current_user.id, payload)
        await self.audit.log_action(
            current_user.id,
            current_user.role,
            "PAYMENT_VERIFIED",
            "payment_orders",
            payload.razorpay_order_id,
            {"purpose": result.get("purpose"), "payment_id": payload.razorpay_payment_id},
        )
        # A paid subscription purchase gets the same thank-you ticket a
        # direct subscribe used to issue (ThankYouPage consumes it).
        sub = result.get("subscription")
        if sub and not result.get("already_processed"):
            result["confirmation_token"] = await self.confirmations.issue(
                "subscription", sub["id"], current_user.id, {"plan_name": sub.get("plan_name")}
            )
        return success(result, "Payment verified")
