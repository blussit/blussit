from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CreateOrderRequest(BaseModel):
    """The client only NAMES what it's paying for — the amount is always
    resolved server-side from the stored booking/plan (see
    PaymentService.create_order)."""
    purpose: Literal["booking", "booking_group", "subscription"]
    booking_id: Optional[str] = None
    # A multi-vehicle visit is paid for ONCE — the order covers every car on
    # it, and one verified signature settles them all.
    booking_group_id: Optional[str] = None
    plan_id: Optional[str] = None
    # A monthly PASS names the car it belongs to and the one service it
    # covers; those two decide the price (UserSubscriptionService.quote_pass),
    # and the CAR's own type is used — never a type the client claims.
    vehicle_id: Optional[str] = None
    service_id: Optional[str] = None
    # LEGACY tier for pre-pass plans (VehicleType id) — prices the order
    # exactly like resolve_plan_price does at create time.
    vehicle_type: Optional[str] = None
    # Subscription purchases only: set up a Razorpay auto-pay mandate so the
    # plan re-bills itself every cycle instead of lapsing. Ignored for
    # bookings (a one-off wash has nothing to renew). If the gateway can't
    # set a mandate up, create-order transparently falls back to a one-time
    # order and says so (`auto_pay_unavailable`).
    auto_pay: bool = False


class CollectPaymentRequest(BaseModel):
    """Captain doorstep settlement — names the booking being settled."""
    booking_id: str = Field(min_length=1, max_length=50)


class VerifyPaymentRequest(BaseModel):
    """Checkout hands back either an ORDER (one-time payment) or a
    SUBSCRIPTION (auto-pay mandate) — exactly one of them, never both, and
    the signature is computed over a different message for each (see
    PaymentService.verify_payment)."""

    razorpay_order_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    razorpay_subscription_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def exactly_one_reference(self) -> "VerifyPaymentRequest":
        if bool(self.razorpay_order_id) == bool(self.razorpay_subscription_id):
            raise ValueError("Send exactly one of razorpay_order_id or razorpay_subscription_id")
        return self

    @property
    def reference_id(self) -> str:
        """Whichever of the two identifies this payment — for logging/audit."""
        return self.razorpay_order_id or self.razorpay_subscription_id or ""
