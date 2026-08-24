from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import ComplaintPriority, ComplaintStatus


class ComplaintCreateRequest(BaseModel):
    booking_id: Optional[str] = None
    subject: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=5, max_length=2000)
    priority: ComplaintPriority = ComplaintPriority.MEDIUM
    attachments: list[str] = []


class ComplaintUpdateRequest(BaseModel):
    status: Optional[ComplaintStatus] = None
    priority: Optional[ComplaintPriority] = None
    resolution_note: Optional[str] = None
