from typing import Optional

from app.models.base import BusinessRecordBase
from app.models.enums import ComplaintPriority, ComplaintStatus


class ComplaintModel(BusinessRecordBase):
    customer_id: str
    booking_id: Optional[str] = None
    service_center_id: Optional[str] = None
    subject: str
    description: str
    priority: ComplaintPriority = ComplaintPriority.MEDIUM
    status: ComplaintStatus = ComplaintStatus.OPEN
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    attachments: list[str] = []
