from datetime import datetime
from typing import Optional

from app.models.base import BusinessRecordBase


class PurchaseConfirmationModel(BusinessRecordBase):
    """A single-purpose, opaque, time-limited ticket for the public
    /thank-you page — deliberately NOT the booking/subscription's own raw
    id. Reachable only as a redirect right after a real purchase succeeds;
    typing a URL by hand (or reusing an old link) can't produce a valid
    token, and even a valid one stops working after expires_at, so the
    confirmation page can never be reached by guessing or bookmarking."""
    token: str
    type: str  # "booking" | "subscription"
    reference_id: str  # the real booking_id / subscription_id, never exposed to the client
    customer_id: Optional[str] = None
    payload: dict = {}  # denormalized display data only (booking_number, plan name, etc.)
    expires_at: datetime
