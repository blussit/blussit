from typing import Optional

from app.models.base import BusinessRecordBase
from app.models.enums import NotificationType


class NotificationModel(BusinessRecordBase):
    user_id: str
    title: str
    message: str
    notification_type: NotificationType = NotificationType.SYSTEM
    is_read: bool = False
    reference_id: Optional[str] = None
