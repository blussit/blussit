import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import NotFoundException
from app.models.enums import NotificationType
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository
from app.services.whatsapp_service import WhatsAppService
from app.utils.serializers import serialize_doc, serialize_list

logger = logging.getLogger(__name__)

# asyncio only holds a WEAK reference to a task it isn't awaited/stored
# somewhere — without this, a fire-and-forget notify(background=True) task
# can get garbage-collected mid-flight before Meta's API even responds.
# Keeping a strong reference here (and dropping it on completion) is the
# standard fix.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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

    async def notify(
        self,
        user_id: str,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.SYSTEM,
        reference_id: str | None = None,
        wa_event: str | None = None,
        wa_params: list[str] | None = None,
        wa_marketing: bool = False,
        background: bool = False,
    ) -> None:
        """`wa_marketing`: the WhatsApp half is a MARKETING template ("book
        again", "we miss you"). Those go out only through their approved
        template and only to customers who haven't opted out — never via
        the generic utility fallback, which would be spam wearing a
        utility badge. The in-app notification is written regardless.

        `background`: the caller's own response (e.g. a booking just
        confirmed) must render before Meta's API even answers — the
        customer sees their Thank You page first, WhatsApp is a distant
        second. Only the outbound WhatsApp call is deferred; the in-app
        notification row is still written before this returns, so anything
        reading it immediately after (a live socket push, a test) sees it.
        Default is False so every other call site keeps its exact existing
        (synchronous, easy to assert on in tests) behaviour."""
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
        if wa_marketing and (user or {}).get("marketing_opt_out"):
            return
        if phone:
            coro = self._send_whatsapp(user_id, phone, title, message, wa_event, wa_params, wa_marketing, reference_id)
            if background:
                _fire_and_forget(coro)
            else:
                await coro

    async def _send_whatsapp(
        self,
        user_id: str,
        phone: str,
        title: str,
        message: str,
        wa_event: str | None,
        wa_params: list[str] | None,
        wa_marketing: bool,
        reference_id: str | None = None,
    ) -> None:
        # Best-effort — a WhatsApp delivery failure must never break
        # the caller's actual business action (a booking/complaint
        # update that already succeeded shouldn't roll back or error
        # out just because the WhatsApp ping failed).
        #
        # Automation engine: call sites that name a wa_event get the
        # dedicated per-event template (blussit_booking_confirmed,
        # blussit_captain_assigned, ...) as soon as Meta approves it;
        # until then — or if the event send fails — the generic update
        # template carries the same information, so nothing goes dark
        # while templates sit in review.
        try:
            sent = False
            if wa_event and wa_params is not None:
                try:
                    from app.services.whatsapp_crm_service import WhatsAppCrmService

                    tpl = await WhatsAppCrmService(self.user_repo.db).event_template_if_ready(wa_event)
                    if tpl:
                        # Only a template actually approved WITH a URL
                        # variable on its button gets one filled in — see
                        # create_template's has_url_param. reference_id is
                        # always the booking id for every event that has
                        # one of these (see BLUSSIT_TEMPLATE_DEFS), which
                        # is exactly what "/app/bookings/{{1}}" expects.
                        button_param = None
                        if tpl.get("has_url_param") and reference_id:
                            button_param = f"{reference_id}?review=1" if wa_event == "service_completed" else reference_id
                        sent = await self.whatsapp.send_event_template(phone, tpl["name"], wa_params, button_param=button_param)
                except Exception:  # noqa: BLE001 — automation must never block the fallback
                    logger.exception("WhatsApp event-template send failed (event=%s, user=%s) — falling back to generic", wa_event, user_id)
                    sent = False
            if wa_marketing:
                return  # approved template or nothing — no utility fallback for marketing
            if not sent:
                delivered = await self.whatsapp.send_generic(phone, title, message)
                if not delivered:
                    # Still best-effort, but LOUD — expired Meta credentials
                    # used to silently stop every customer message while the
                    # in-app rows kept the dashboards looking healthy.
                    logger.warning("WhatsApp send failed (user=%s, title=%r) — message delivered in-app only", user_id, title)
        except Exception:  # noqa: BLE001 — this may be running unawaited in the background
            logger.exception("Unexpected error sending WhatsApp notification (user=%s, title=%r)", user_id, title)

    async def list_for_user(self, user_id: str, page: int, page_size: int, unread_only: bool = False):
        items, total = await self.repo.list_for_user(user_id, page, page_size, unread_only)
        return serialize_list(items), total

    async def unread_count(self, user_id: str) -> int:
        return await self.repo.unread_count(user_id)

    async def mark_read(self, user_id: str, notification_id: str) -> dict:
        # Owner-scoped: a notification id belonging to someone else is a
        # 404, never a read-and-return of their document (that was a real
        # IDOR — any user could read anyone's alerts by id).
        updated = await self.repo.mark_read_for_user(notification_id, user_id)
        if not updated:
            raise NotFoundException("Notification not found")
        return serialize_doc(updated)

    async def mark_all_read(self, user_id: str) -> None:
        await self.repo.mark_all_read(user_id)
