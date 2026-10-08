from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.payment_schema import (
    CollectPaymentRequest,
    CreateOrderRequest,
    PaymentFailureReport,
    ResolveAttentionRequest,
    VerifyPaymentRequest,
)
from app.schemas.subscription_schema import ManagerSubscriptionOfferRequest, ManagerSubscriptionPreviewRequest
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
        result = await self.service.captain_collect_cash(payload.booking_id, current_user.id, payload.expected_amount)
        await self.audit.log_action(
            current_user.id, current_user.role, "CASH_COLLECTED", "bookings", payload.booking_id,
            {"amount": result.get("amount"), "wallet_credit": result.get("wallet_credit")},
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
            payload.reference_id,
            {"purpose": result.get("purpose"), "payment_id": payload.razorpay_payment_id},
        )
        await self._attach_confirmation(result, current_user.id)
        return success(result, "Payment verified")

    async def _attach_confirmation(self, result: dict, customer_id: str) -> None:
        # A paid subscription purchase gets the same thank-you ticket a
        # direct subscribe used to issue (ThankYouPage consumes it) — once,
        # to whichever request first confirms it (verify, or the status poll
        # after a verify that never got through).
        sub = result.get("subscription")
        if sub and result.pop("first_confirmation", False):
            result["confirmation_token"] = await self.confirmations.issue(
                "subscription", sub["id"], customer_id, {"plan_name": sub.get("plan_name")}
            )
        result.pop("first_confirmation", None)

    async def report_failure(self, current_user: CurrentUser, payload: PaymentFailureReport):
        return success(await self.service.record_payment_failure(current_user.id, payload))

    async def status(self, current_user: CurrentUser, order_id: str | None, subscription_id: str | None):
        result = await self.service.payment_status_for_customer(current_user.id, order_id, subscription_id)
        await self._attach_confirmation(result, current_user.id)
        return success(result)

    async def booking_state(self, current_user: CurrentUser, booking_id: str):
        return success(await self.service.booking_payment_state(current_user.id, booking_id))

    async def resolve_attention(self, current_user: CurrentUser, order_id: str, payload: ResolveAttentionRequest):
        result = await self.service.resolve_attention(
            order_id, current_user.id, payload.note, outcome=payload.outcome, refund_amount=payload.refund_amount,
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "PAYMENT_ATTENTION_RESOLVED", "payment_orders", order_id,
            {"note": payload.note, "outcome": payload.outcome, "refund_amount": payload.refund_amount},
        )
        return success(result, "Marked as resolved")

    # -- Manager selling a plan (WhatsApp link / auto-pay / cash) --------

    async def manager_offer_preview(self, current_user: CurrentUser, payload: ManagerSubscriptionPreviewRequest):
        return success(await self.service.manager_subscription_preview(payload, actor_role=current_user.role))

    async def manager_offer_create(self, current_user: CurrentUser, payload: ManagerSubscriptionOfferRequest):
        from app.core.authz import resolve_grant_center_id

        center_id = resolve_grant_center_id(current_user.role, current_user.service_center_id, payload.service_center_id)
        result = await self.service.manager_subscription_offer(
            current_user.id, payload, actor_center_id=center_id, actor_role=current_user.role,
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "MANAGER_SUBSCRIPTION_OFFER", "payment_orders",
            result.get("order_id") or (result.get("subscription") or {}).get("id"),
            {
                "plan_id": payload.plan_id, "recurring": payload.recurring, "payment_method": payload.payment_method,
                "discount_amount": payload.discount_amount, "coupon_code": payload.coupon_code, "amount": result.get("amount"),
            },
        )
        message = (
            "Auto-pay link sent" if result["kind"] == "autopay"
            else "Plan activated — cash recorded" if result["kind"] == "cash"
            else "Payment link sent"
        )
        return success(result, message)

    async def manager_offer_void(self, current_user: CurrentUser, order_id: str):
        result = await self.service.void_manager_subscription_offer(
            order_id, current_user.id, current_user.role, current_user.service_center_id
        )
        await self.audit.log_action(current_user.id, current_user.role, "MANAGER_SUBSCRIPTION_OFFER_VOIDED", "payment_orders", order_id)
        return success(result, "Offer cancelled" if result.get("voided") else "This offer was already settled or cancelled")
