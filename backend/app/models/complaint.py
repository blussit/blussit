from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.base import BusinessRecordBase
from app.models.enums import ComplaintPriority, ComplaintStatus


class ComplaintReply(BaseModel):
    """One entry in a complaint's reply thread — a manager/admin's insight
    or update on what's being done/was resolved. Append-only (never
    edited/removed once posted), so the customer and any other staff member
    can see the full history of what happened, not just the latest note."""
    author_id: str
    author_role: str
    message: str
    created_at: datetime


class ComplaintModel(BusinessRecordBase):
    customer_id: str
    # Every complaint must be tied to a real, specific booking of the
    # customer's own — never a free-floating complaint about nothing in
    # particular. This is what lets a complaint route deterministically to
    # the manager of the service center that booking belongs to (via
    # service_center_id, snapshotted from the booking below), instead of
    # requiring a customer to somehow pick "the right" center themselves.
    booking_id: str
    service_center_id: Optional[str] = None
    subject: str
    description: str
    priority: ComplaintPriority = ComplaintPriority.MEDIUM
    status: ComplaintStatus = ComplaintStatus.OPEN
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    attachments: list[str] = []
    replies: list[ComplaintReply] = []
