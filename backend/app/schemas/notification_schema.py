from typing import Optional

from pydantic import BaseModel, Field


class NotificationPreferencesUpdate(BaseModel):
    """The manager's "WhatsApp me new bookings" switch. `user_id`: an
    admin switching someone else's (a manager can only change their own)."""

    whatsapp_new_booking_alerts: bool
    user_id: Optional[str] = Field(default=None, max_length=64)


class UniversalMessageRequest(BaseModel):
    """Admin / manager -> one customer, sent through the universal_message
    WhatsApp template ("Hi {name}, {message} — Team Blussit")."""

    customer_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class WhatsAppSettingsUpdate(BaseModel):
    """Admin WhatsApp settings. google_review_url: https:// only ("" clears
    it and stops the review-request sweep)."""

    google_review_url: Optional[str] = Field(default=None, max_length=500)
