from typing import Optional

from app.models.base import BusinessRecordBase


class FaqModel(BusinessRecordBase):
    question: str
    answer: str
    display_order: int = 0
    is_active: bool = True


class TestimonialModel(BusinessRecordBase):
    customer_name: str
    customer_image: Optional[str] = None
    rating: int = 5
    comment: str
    is_featured: bool = True
    display_order: int = 0


class SettingModel(BusinessRecordBase):
    key: str
    value: dict
    description: Optional[str] = None
