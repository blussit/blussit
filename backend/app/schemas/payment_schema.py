from typing import Literal, Optional

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    """The client only NAMES what it's paying for — the amount is always
    resolved server-side from the stored booking/plan (see
    PaymentService.create_order)."""
    purpose: Literal["booking", "subscription"]
    booking_id: Optional[str] = None
    plan_id: Optional[str] = None
    # Subscription tier being purchased (VehicleType id) — prices the order
    # exactly like resolve_plan_price does at create time.
    vehicle_type: Optional[str] = None


class CollectPaymentRequest(BaseModel):
    """Captain doorstep settlement — names the booking being settled."""
    booking_id: str = Field(min_length=1, max_length=50)


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=100)
    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)
