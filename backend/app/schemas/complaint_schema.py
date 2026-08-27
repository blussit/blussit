from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import ComplaintPriority, ComplaintStatus


class ComplaintCreateRequest(BaseModel):
    # Required — a customer picks one of their own existing bookings, never
    # types a free-floating complaint. ComplaintService.create re-verifies
    # this booking actually belongs to the requesting customer.
    booking_id: str
    subject: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=5, max_length=2000)
    priority: ComplaintPriority = ComplaintPriority.MEDIUM
    attachments: list[str] = []


class ComplaintUpdateRequest(BaseModel):
    status: Optional[ComplaintStatus] = None
    priority: Optional[ComplaintPriority] = None
    resolution_note: Optional[str] = None


class ComplaintReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    status: Optional[ComplaintStatus] = None
