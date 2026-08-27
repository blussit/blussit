from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.enums import NotificationType
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository
from app.services.whatsapp_service import WhatsAppService
from app.utils.serializers import serialize_doc, serialize_list


class NotificationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.repo = NotificationRepository(db)
        # Every in-app notification also goes out on WhatsApp when the
        # recipient has a phone on file — a single bridge point here means
        # every one of this app's ~30 existing notify() call sites (booking
        # updates, complaint replies, assignment changes, etc.) gets
        # WhatsApp delivery for free, with no per-call-site changes needed.
        self.user_repo = UserRepository(db)
        self.whatsapp = WhatsAppService(db)

    async def notify(self, user_id: str, title: str, message: str, notification_type: NotificationType = NotificationType.SYSTEM, reference_id: str | None = None) -> None:
        await self.repo.create(
            {
                "user_id": user_id,
                "title": title,
                "message": message,
                "notification_type": notification_type.value if hasattr(notification_type, "value") else notification_type,
                "reference_id": reference_id,
                "is_read": False,
            }
        )
        user = await self.user_repo.find_by_id(user_id)
        phone = (user or {}).get("phone")
        if phone:
            # Best-effort — a WhatsApp delivery failure must never break
            # the caller's actual business action (a booking/complaint
            # update that already succeeded shouldn't roll back or error
            # out just because the WhatsApp ping failed).
            await self.whatsapp.send_generic(phone, title, message)

    async def list_for_user(self, user_id: str, page: int, page_size: int, unread_only: bool = False):
        items, total = await self.repo.list_for_user(user_id, page, page_size, unread_only)
        return serialize_list(items), total

    async def unread_count(self, user_id: str) -> int:
        return await self.repo.unread_count(user_id)

    async def mark_read(self, user_id: str, notification_id: str) -> dict:
        updated = await self.repo.update_by_id(notification_id, {"is_read": True})
        return serialize_doc(updated)

    async def mark_all_read(self, user_id: str) -> None:
        await self.repo.mark_all_read(user_id)
