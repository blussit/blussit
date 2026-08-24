from typing import Optional

from app.models.base import BusinessRecordBase


class ReviewModel(BusinessRecordBase):
    booking_id: str
    customer_id: str
    captain_id: Optional[str] = None
    service_center_id: Optional[str] = None
    rating: int
    comment: Optional[str] = None
    is_published: bool = True
