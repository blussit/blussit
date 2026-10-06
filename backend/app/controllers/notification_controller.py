import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class NotificationController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = NotificationService(db)

    async def list_mine(self, current_user: CurrentUser, pagination: PaginationParams, unread_only: bool):
        if current_user.role == "manager":
            # A manager's bell is a to-do list: alerts about work already
            # done clear themselves, and read ones drop off the list.
            try:
                await self.service.clear_handled_alerts(current_user.id)
            except Exception:  # noqa: BLE001 — the list must still load
                logger.exception("Could not clear handled alerts for manager %s", current_user.id)
            unread_only = True
        items, total = await self.service.list_for_user(current_user.id, pagination.page, pagination.page_size, unread_only)
        unread = await self.service.unread_count(current_user.id)
        result = paginated(items, pagination.page, pagination.page_size, total)
        result["unread_count"] = unread
        return result

    async def mark_read(self, current_user: CurrentUser, notification_id: str):
        result = await self.service.mark_read(current_user.id, notification_id)
        return success(result, "Notification marked as read")

    async def mark_all_read(self, current_user: CurrentUser):
        await self.service.mark_all_read(current_user.id)
        return success(None, "All notifications marked as read")
