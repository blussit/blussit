"""
The WhatsApp booking bot — a full conversational booking flow driven by
Meta webhook messages, deliberately built as a thin CLIENT of the exact
same service layer the web app and manager portal use, never a parallel
system:

  - Slot availability shown in chat comes from BookingService.
    available_slots — the same per-date counters the website shows, so an
    admin capacity/policy change (or a slot filling up on the web) is
    reflected in the very next chat message.
  - The booking itself is created through BookingService.create_booking —
    the same atomic capacity reservation (increment_if), same fraud
    checks, same slot validation, same manager notification. A WhatsApp
    booking therefore reduces the same capacity pool web bookings do, and
    two customers (one on the site, one on WhatsApp) can never overbook
    the same last spot.
  - It lands in the same bookings collection, so it appears in the manager
    queue / admin drill-down automatically — only tagged source="whatsapp"
    so staff can see where it came from.

Account management per WhatsApp number: the sender's wa_id IS a verified
phone number (Meta verified it when the WhatsApp account was created), so
the bot links it to the existing customer account with that phone. For a
brand-new number, it asks for the customer's name before creating the
account (no usable password — customers log in to the website by OTP).

The booking conversation (quick-booking model, 2026-09) is deliberately
short: vehicle type → service + price → day/time → location → confirm.
WhatsApp takes one vehicle per booking. No quantity question, no "add
another", no plates, no brand/model, no OTP — the same
BookingService.create_quick_booking the website uses.

Conversation state lives in the whatsapp_conversations collection (one doc
per wa_id); processed message ids are recorded in whatsapp_message_dedup
(unique index + TTL) because Meta redelivers webhooks on retry and a
retried "Confirm" tap must not create a second booking.
"""
import logging
import random
import re
import string
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import AppException
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.repositories.address_repository import AddressRepository
from app.repositories.booking_repository import BookingRepository
from app.repositories.catalog_repository import ServiceRepository
from app.repositories.user_repository import UserRepository
from app.repositories.vehicle_repository import VehicleRepository
from app.repositories.vehicle_type_repository import VehicleTypeRepository
from app.schemas.booking_schema import BookingCancelRequest, BookingRescheduleRequest, QuickBookingLine, QuickBookingRequest
from app.schemas.profile_schema import AddressCreateRequest
from app.services.booking_service import BookingService
from app.services.profile_service import AddressService
from app.services.whatsapp_service import WhatsAppService
from app.utils.timezone import now_ist

logger = logging.getLogger(__name__)

# A conversation left idle this long starts over on the next message —
# a customer replying to yesterday's half-finished flow shouldn't land in
# the middle of a stale slot pick.
CONVERSATION_TTL_MINUTES = 30

_RESET_WORDS = {"cancel", "stop", "restart", "reset"}
_GREETING_WORDS = {"hi", "hello", "hey", "menu", "start", "namaste"}


def _short(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class WhatsAppBotService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.conversations = db["whatsapp_conversations"]
        self.dedup = db["whatsapp_message_dedup"]
        self.wa = WhatsAppService(db)
        self.users = UserRepository(db)
        self.vehicles = VehicleRepository(db)
        self.addresses = AddressRepository(db)
        self.services = ServiceRepository(db)
        self.vehicle_types = VehicleTypeRepository(db)
        self.bookings = BookingRepository(db)
        self.booking_service = BookingService(db)
        self.address_service = AddressService(db)

    # ------------------------------------------------------------------
    # Webhook entry point
    # ------------------------------------------------------------------

    async def handle_webhook(self, payload: dict) -> dict:
        """Processes one Meta webhook POST. Never raises — Meta retries
        non-200 responses for up to 7 days, so an internal error must be
        swallowed (logged + apologetic chat reply) rather than turned into
        an endless redelivery loop of the same crashing message."""
        processed = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                if change.get("field") != "messages":
                    continue
                # Delivery receipts for OUR outbound messages — the only
                # place Meta ever reports a silent drop (e.g. free-form
                # text to a number with no open 24h session: the send API
                # returns 200, then a `failed` status with error 131047
                # arrives here). Written back onto the matching outbox row
                # so "accepted but never delivered" is visible instead of
                # masquerading as success.
                for status in value.get("statuses") or []:
                    wamid = status.get("id")
                    if not wamid:
                        continue
                    update: dict = {"delivery_status": status.get("status")}
                    if status.get("errors"):
                        update["delivery_errors"] = [
                            {"code": e.get("code"), "title": e.get("title"), "message": e.get("message")} for e in status["errors"]
                        ]
                        logger.warning("WhatsApp delivery failed for %s: %s", wamid, update["delivery_errors"])
                    await self.db.whatsapp_outbox.update_one({"wamid": wamid}, {"$set": update})
                contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
                for message in value.get("messages") or []:
                    wa_id = message.get("from")
                    wamid = message.get("id")
                    if not wa_id or not wamid:
                        continue
                    if not await self._first_time_seeing(wamid):
                        continue  # Meta redelivery — already handled
                    # CRM inbox recording happens FIRST and independently:
                    # an agent must see the message even if the bot's
                    # state machine errors on it. record_inbound never
                    # raises (see WhatsAppCrmService).
                    from app.services.whatsapp_crm_service import WhatsAppCrmService

                    crm = WhatsAppCrmService(self.db)
                    await crm.record_inbound(wa_id, contacts.get(wa_id), message)
                    if await crm.is_bot_paused(wa_id):
                        # A human agent owns this thread (they sent a
                        # manual message and haven't resolved it) — the
                        # bot stays silent instead of talking over them.
                        processed += 1
                        continue
                    try:
                        await self._handle_message(wa_id, contacts.get(wa_id), message)
                        processed += 1
                    except Exception:  # noqa: BLE001 — see docstring
                        logger.exception("WhatsApp bot failed handling %s from %s", wamid, wa_id)
                        await self._safe_reply(wa_id, "Sorry, something went wrong on our side. Type *hi* to start again.")
                        await self._set_state(wa_id, None, {})
        return {"processed": processed}

    async def _first_time_seeing(self, wamid: str) -> bool:
        try:
            await self.dedup.insert_one({"wamid": wamid, "created_at": datetime.now(timezone.utc)})
            return True
        except DuplicateKeyError:
            return False

    async def _safe_reply(self, wa_id: str, text: str) -> None:
        try:
            await self.wa.send_text(self._local_phone(wa_id), text)
        except Exception:  # noqa: BLE001
            logger.exception("Failed sending WhatsApp reply to %s", wa_id)

    # ------------------------------------------------------------------
    # Conversation plumbing
    # ------------------------------------------------------------------

    @staticmethod
    def _local_phone(wa_id: str) -> str:
        """wa_id arrives as E.164 digits (919302964803). The app stores
        Indian numbers as bare 10-digit strings — strip the 91 country
        code when that's what this is, otherwise keep as-is."""
        digits = "".join(ch for ch in wa_id if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            return digits[2:]
        return digits

    async def _load_conversation(self, wa_id: str) -> dict:
        convo = await self.conversations.find_one({"wa_id": wa_id})
        if convo:
            updated_at = convo.get("updated_at")
            if updated_at and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at and datetime.now(timezone.utc) - updated_at > timedelta(minutes=CONVERSATION_TTL_MINUTES):
                # Mid-flow timeout: flag it so the menu greeting can
                # acknowledge the restart instead of silently forgetting.
                convo["_timed_out"] = bool(convo.get("state"))
                convo["state"], convo["data"] = None, {}
            return convo
        return {"wa_id": wa_id, "state": None, "data": {}, "customer_id": None}

    async def _set_state(self, wa_id: str, state: str | None, data: dict, customer_id: str | None = None) -> None:
        update: dict = {"state": state, "data": data, "updated_at": datetime.now(timezone.utc)}
        if customer_id:
            update["customer_id"] = customer_id
            update["phone"] = self._local_phone(wa_id)
        await self.conversations.update_one({"wa_id": wa_id}, {"$set": update, "$setOnInsert": {"wa_id": wa_id}}, upsert=True)

    async def _ensure_customer(self, wa_id: str, profile_name: str | None, supplied_name: str | None = None) -> tuple[str | None, bool]:
        """Links this WhatsApp number to its customer account. A brand-new
        number is created only after the customer has typed their name.
        Either way, the account ends up phone_verified because messaging
        us from that number proves phone ownership."""
        phone = self._local_phone(wa_id)
        user = await self.users.find_by_phone(phone)
        if not user and phone != wa_id:
            user = await self.users.find_by_phone(wa_id)
        if user:
            # Messaging us from this number IS live proof of ownership —
            # every chat refreshes the 90-day verification window.
            await self.users.update_by_id(str(user["_id"]), {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)})
            return str(user["_id"]), False

        name = (supplied_name or "").strip()
        if not name:
            return None, True
        password = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        prefix = "".join(ch for ch in name.upper() if ch.isalpha())[:4] or "USER"
        try:
            created = await self.users.create({
                "full_name": name,
                "phone": phone,
                "password_hash": hash_password(password),
                "role": UserRole.CUSTOMER.value,
                "status": UserStatus.ACTIVE.value,
                "referral_code": f"{prefix}{''.join(random.choices(string.digits, k=4))}",
                # The random password above is never shown to anyone —
                # customers log in by phone OTP (quick-booking model), so
                # there is no password to "change" and no gate to trip.
                "phone_verified": True,
                "phone_verified_at": datetime.now(timezone.utc),
                "account_source": "whatsapp",
            })
        except DuplicateKeyError:
            user = await self.users.find_by_phone(phone)
            if not user and phone != wa_id:
                user = await self.users.find_by_phone(wa_id)
            if user:
                await self.users.update_by_id(str(user["_id"]), {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc)})
                return str(user["_id"]), False
            raise
        return str(created["_id"]), True

    @staticmethod
    def _extract_input(message: dict) -> tuple[str, object]:
        """Returns (kind, value): ("text", str) | ("reply", row-or-button
        id) | ("location", (lat, lng)) | ("unsupported", "")."""
        mtype = message.get("type")
        if mtype == "text":
            return "text", (message.get("text") or {}).get("body", "").strip()
        if mtype == "interactive":
            inter = message.get("interactive") or {}
            reply = inter.get("list_reply") or inter.get("button_reply") or {}
            return "reply", reply.get("id", "")
        if mtype == "location":
            loc = message.get("location") or {}
            return "location", (loc.get("latitude"), loc.get("longitude"))
        return "unsupported", ""

    # ------------------------------------------------------------------
    # The state machine
    # ------------------------------------------------------------------

    async def _handle_message(self, wa_id: str, profile_name: str | None, message: dict) -> None:
        phone = self._local_phone(wa_id)
        convo = await self._load_conversation(wa_id)
        kind, value = self._extract_input(message)
        text_lower = value.lower() if kind == "text" else ""

        # Global escape hatches beat whatever state we were in.
        if kind == "text" and text_lower in _RESET_WORDS:
            stale = convo.get("data") or {}
            if convo.get("customer_id") and stale.get("center_id") and stale.get("date") and stale.get("slot"):
                await self.booking_service.release_hold(convo["customer_id"], stale["center_id"], stale["date"], stale["slot"])
            await self._set_state(wa_id, None, {})
            await self.wa.send_text(phone, "Okay, cancelled. Type *hi* whenever you want to book.")
            return
        # "back"/"menu" from anywhere returns to the main menu — the
        # universal recovery from a mis-tap without losing the account.
        if kind == "text" and text_lower in ("back", "menu", "start over", "restart"):
            await self._send_menu(wa_id, phone)
            return

        customer_id = convo.get("customer_id")
        if not customer_id:
            customer_id, is_new = await self._ensure_customer(wa_id, profile_name)
            if not customer_id:
                data = convo.get("data") or {}
                if convo.get("state") == "collect_name":
                    name_text = str(value).strip()
                    if kind != "text" or len(name_text) < 2 or name_text.lower() in _GREETING_WORDS:
                        await self.wa.send_text(phone, "Please reply with your name so we can save your booking correctly.")
                        await self._set_state(wa_id, "collect_name", data)
                        return
                    customer_id, is_new = await self._ensure_customer(wa_id, profile_name, name_text)
                    convo["customer_id"] = customer_id
                    await self._set_state(wa_id, None, {}, customer_id=customer_id)
                    await self.wa.send_text(phone, f"Thanks {name_text.split()[0]}! Your WhatsApp number is verified. ✅")
                    await self._send_menu(wa_id, phone)
                    return
                await self._set_state(wa_id, "collect_name", {})
                await self.wa.send_text(phone, "Welcome to Blussit! Please tell us your name to get started.")
                return
            convo["customer_id"] = customer_id
            # Persist the number→account link on the conversation doc
            # itself, immediately — this IS the per-WhatsApp-number account
            # record, and without writing it here every later message
            # would re-resolve the account and the doc would never say
            # which customer this number belongs to.
            await self._set_state(wa_id, convo.get("state"), convo.get("data") or {}, customer_id=customer_id)
            if is_new:
                await self.wa.send_text(
                    phone,
                    "Welcome to Blussit! Your WhatsApp number is verified. ✅",
                )

        state = convo.get("state")
        data = convo.get("data") or {}

        # Stateless payment buttons ("pay:online:<id>" / "pay:cash:<id>") —
        # sent right after a booking confirms, but honored whenever tapped,
        # whatever flow state the chat has moved to since.
        if kind == "reply" and str(value).startswith("pay:"):
            await self._on_pay_choice(phone, customer_id, str(value))
            return

        # No active flow (or an explicit greeting) → main menu.
        if state is None or (kind == "text" and text_lower in _GREETING_WORDS):
            if convo.get("_timed_out"):
                await self.wa.send_text(phone, "Looks like our last chat timed out — no worries, let's start fresh. Your saved vehicles and addresses are still here. 👍")
            await self._send_menu(wa_id, phone)
            return

        handler = {
            "menu": self._on_menu,
            "q_type": self._on_type,
            "q_service": self._on_service,
            "q_address": self._on_address,
            "await_location": self._on_await_location,
            "await_addr_pincode": self._on_await_addr_pincode,
            "q_when": self._on_when,
            "confirm": self._on_confirm,
            "bookings_list": self._on_bookings_list,
            "booking_detail": self._on_booking_detail,
            "resched_date": self._on_resched_date,
            "resched_slot": self._on_resched_slot,
            "cancel_confirm": self._on_cancel_confirm,
        }.get(state)
        if handler is None:
            await self._send_menu(wa_id, phone)
            return
        await handler(wa_id, phone, customer_id, data, kind, value)

    # -- confusion handling / admin handoff ---------------------------

    async def _nudge(self, wa_id: str, phone: str, state: str, data: dict, message: str, user_text: str = "") -> None:
        """Re-asks once; a SECOND consecutive invalid input means the
        customer is out of the bot's depth — hand the thread to a human
        (CRM inbox) instead of looping the same prompt forever."""
        strikes = int(data.get("strikes", 0)) + 1
        if strikes >= 2:
            await self._handoff_to_admin(wa_id, phone, user_text or message)
            return
        data["strikes"] = strikes
        await self._set_state(wa_id, state, data)
        await self.wa.send_text(phone, message + "\n\n(Or type *menu* to start over.)")

    async def _handoff_to_admin(self, wa_id: str, phone: str, context_text: str) -> None:
        """Pauses the bot for this thread and pings the admins — the
        conversation continues with a human in the admin WhatsApp inbox."""
        await self.conversations.update_one(
            {"wa_id": wa_id},
            {"$set": {"bot_paused": True, "state": None, "data": {}, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        await self.wa.send_text(
            phone,
            "Let me get a real person to help you with this. 👤 Our team has been notified and will reply right here shortly.",
        )
        await self._ping_admins(wa_id, phone, context_text)

    async def _ping_admins(self, wa_id: str, phone: str, context_text: str) -> None:
        try:
            from app.models.enums import NotificationType
            from app.services.notification_service import NotificationService

            notifications = NotificationService(self.db)
            async for admin in self.db.users.find({"role": "admin", "is_deleted": {"$ne": True}}).limit(3):
                await notifications.notify(
                    str(admin["_id"]),
                    "WhatsApp customer needs help",
                    f"+{wa_id}: {_short(context_text, 120)} — reply from the admin WhatsApp inbox.",
                    NotificationType.SYSTEM,
                )
        except Exception:  # noqa: BLE001 — a failed ping must not break the chat
            logger.exception("Failed pinging admins for WhatsApp handoff")

    # -- menu ----------------------------------------------------------

    async def _send_menu(self, wa_id: str, phone: str) -> None:
        buttons = [{"id": "menu:book", "title": "📅 Book a service"}, {"id": "menu:bookings", "title": "🧾 My bookings"}]
        # A returning customer's fastest path: replay the last visit (same
        # vehicles, services and address) and only pick a new time.
        convo = await self.conversations.find_one({"wa_id": wa_id}) or {}
        if convo.get("customer_id") and await self._last_visit(convo["customer_id"]):
            buttons.insert(0, {"id": "menu:again", "title": "🔁 Book again"})
        await self.wa.send_buttons(phone, "What would you like to do?", buttons)
        await self._set_state(wa_id, "menu", {})

    async def _on_menu(self, wa_id, phone, customer_id, data, kind, value):
        choice = value if kind == "reply" else f"menu:{value.lower()}" if kind == "text" else ""
        if choice in ("menu:book", "menu:book a service", "menu:book service", "menu:1"):
            await self._start_type_step(wa_id, phone, customer_id, {"lines": []})
        elif choice in ("menu:again", "menu:book again", "menu:repeat"):
            await self._book_again(wa_id, phone, customer_id, {"lines": []})
        elif choice in ("menu:bookings", "menu:my bookings", "menu:2"):
            items, _ = await self.bookings.find_many(
                {"customer_id": customer_id, "status": {"$in": ["pending", "assigned", "captain_on_the_way", "service_started", "rescheduled"]}},
                page=1, page_size=5, sort_by="scheduled_date", sort_order=1,
            )
            if not items:
                await self.wa.send_text(phone, "You have no upcoming bookings. Tap *Book a service* to make one!")
                await self._send_menu(wa_id, phone)
                return
            await self._send_bookings_list(wa_id, phone, items)
        else:
            # Free text that isn't a menu action = a question the bot
            # can't answer. Ping the admins (once per attempt), keep the
            # menu available, and let a human pick it up in the inbox.
            courtesy = {"ok", "okay", "thanks", "thank you", "thx", "great", "good", "cool", "👍", "🙏", "yes", "no"}
            if kind == "text" and len(value.strip()) > 2 and value.strip().lower() not in courtesy:
                await self._ping_admins(wa_id, phone, value)
                await self.wa.send_text(
                    phone,
                    "I can help with *bookings* right here — for anything else, our team has been notified and will reply to you shortly. 👤",
                )
            await self._send_menu(wa_id, phone)

    # -- my bookings: readable list + per-booking actions --------------

    async def _service_names_for(self, booking: dict) -> str:
        names = []
        for sid in booking.get("service_ids") or []:
            svc = await self.services.find_by_id(sid)
            if svc:
                names.append(svc.get("name", ""))
        return ", ".join(n for n in names if n) or "Service"

    @staticmethod
    def _fmt_date(booking: dict) -> str:
        d = booking.get("scheduled_date")
        try:
            return d.strftime("%a %d %b")
        except AttributeError:
            return str(d)[:10]

    async def _send_bookings_list(self, wa_id: str, phone: str, items: list[dict]) -> None:
        rows = []
        for b in items[:10]:
            names = await self._service_names_for(b)
            rows.append({
                "id": f"bk:{b['_id']}",
                "title": _short(f"{b['booking_number']} · {self._fmt_date(b)}"),
                "description": _short(f"{b.get('scheduled_slot')} · {names} · {b.get('status', '').replace('_', ' ').title()}", 72),
            })
        await self.wa.send_list(phone, "Your upcoming bookings — tap one for details:", "My bookings", rows)
        await self._set_state(wa_id, "bookings_list", {})

    async def _on_bookings_list(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("bk:"):
            await self._nudge(wa_id, phone, "bookings_list", data, "Please tap a booking from the list above 👆", str(value))
            return
        await self._show_booking_detail(wa_id, phone, customer_id, value.split(":", 1)[1])

    async def _show_booking_detail(self, wa_id, phone, customer_id, booking_id: str) -> None:
        booking = await self.bookings.find_by_id(booking_id)
        if not booking or booking.get("customer_id") != customer_id:
            await self.wa.send_text(phone, "That booking isn't on your account any more.")
            await self._send_menu(wa_id, phone)
            return
        names = await self._service_names_for(booking)
        status = booking.get("status", "").replace("_", " ").title()
        lines = [
            f"🧾 *{booking['booking_number']}*",
            f"📌 Status: *{status}*",
            "",
            f"📅 {self._fmt_date(booking)} · ⏰ {booking.get('scheduled_slot')}",
            f"🧽 {names}",
        ]
        vehicle = await self.vehicles.find_by_id(booking.get("vehicle_id", "")) if booking.get("vehicle_id") else None
        if vehicle:
            lines.append(f"🚗 {vehicle.get('brand', '')} {vehicle.get('model', '')} · {vehicle.get('registration_number', '')}".strip())
        address = await self.addresses.find_by_id(booking.get("address_id", "")) if booking.get("address_id") else None
        if address:
            addr_bits = ", ".join(x for x in (address.get("line1"), address.get("city"), address.get("pincode")) if x)
            lines.append(f"📍 {_short(addr_bits, 90)}")
        if booking.get("captain_id"):
            captain = await self.users.find_by_id(booking["captain_id"])
            if captain:
                lines.append(f"🧑‍🔧 Captain: {captain.get('full_name', 'Assigned')}")
        else:
            lines.append("🧑‍🔧 Captain: not assigned yet")
        pay = "Covered by subscription" if booking.get("payment_method") == "subscription" else f"₹{booking.get('total_amount')} · pay after service"
        lines.append(f"💰 {pay}")
        source = booking.get("source")
        if source and source != "app":
            lines.append(f"🏷 Booked via {'WhatsApp' if source == 'whatsapp' else 'our team'}")
        detail = "\n".join(lines)
        await self.wa.send_buttons(phone, detail, [
            {"id": "bkact:resched", "title": "🔁 Reschedule"},
            {"id": "bkact:cancel", "title": "❌ Cancel booking"},
            {"id": "bkact:back", "title": "⬅️ Back"},
        ])
        await self._set_state(wa_id, "booking_detail", {"booking_id": booking_id})

    async def _on_booking_detail(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("bkact:"):
            await self._nudge(wa_id, phone, "booking_detail", data, "Tap one of the buttons above 👆", str(value))
            return
        action = value.split(":", 1)[1]
        if action == "back":
            await self._send_menu(wa_id, phone)
            return
        booking = await self.bookings.find_by_id(data.get("booking_id", ""))
        if not booking or booking.get("customer_id") != customer_id:
            await self._send_menu(wa_id, phone)
            return
        if action == "cancel":
            await self.wa.send_buttons(
                phone,
                f"Cancel booking *{booking['booking_number']}* ({self._fmt_date(booking)} · {booking.get('scheduled_slot')})?",
                [{"id": "cxl:yes", "title": "Yes, cancel it"}, {"id": "cxl:no", "title": "Keep booking"}],
            )
            await self._set_state(wa_id, "cancel_confirm", data)
            return
        # Reschedule — same policy checks as the website, enforced by
        # BookingService.reschedule_booking at the end of this flow.
        data["resched_center_id"] = booking["service_center_id"]
        today = now_ist().date()
        labels = ["Today", "Tomorrow", (today + timedelta(days=2)).strftime("%a %d %b")]
        buttons = [{"id": f"rdate:{(today + timedelta(days=i)).isoformat()}", "title": labels[i]} for i in range(3)]
        await self.wa.send_buttons(phone, "Pick the new day:", buttons)
        await self._set_state(wa_id, "resched_date", data)

    async def _on_resched_date(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("rdate:"):
            await self._nudge(wa_id, phone, "resched_date", data, "Please pick the new day using the buttons above 👆", str(value))
            return
        data["resched_date"] = value.split(":", 1)[1]
        slots = await self.booking_service.available_slots(data["resched_center_id"], data["resched_date"])
        open_slots = [x for x in slots if x["status"] != "full"]
        if not open_slots:
            await self.wa.send_text(phone, "That day is fully booked 😔 — try another day.")
            return
        rows = [{"id": f"rslot:{x['key']}", "title": f"{x['start']}–{x['end']}",
                 "description": "Available" if x["status"] == "available" else f"Only {x['remaining']} spot(s) left"}
                for x in open_slots[:10]]
        await self.wa.send_list(phone, "Pick the new time slot:", "New slot", rows)
        await self._set_state(wa_id, "resched_slot", data)

    async def _on_resched_slot(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("rslot:"):
            await self._nudge(wa_id, phone, "resched_slot", data, "Please pick a time slot from the list above 👆", str(value))
            return
        slot = value.split(":", 1)[1]
        try:
            updated = await self.booking_service.reschedule_booking(
                data["booking_id"],
                BookingRescheduleRequest(scheduled_date=datetime.strptime(data["resched_date"], "%Y-%m-%d"), scheduled_slot=slot),
                actor_id=customer_id,
                actor_role="customer",
            )
        except AppException as exc:
            await self.wa.send_text(phone, f"Couldn't reschedule: {exc.message}")
            await self._send_menu(wa_id, phone)
            return
        await self._set_state(wa_id, None, {})
        await self.wa.send_text(
            phone,
            f"✅ Rescheduled!\n\n🧾 *{updated['booking_number']}*\n📅 {data['resched_date']} · {slot}\n\nType *hi* for the menu.",
        )

    async def _on_cancel_confirm(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("cxl:"):
            await self._nudge(wa_id, phone, "cancel_confirm", data, "Tap *Yes, cancel it* or *Keep booking* above 👆", str(value))
            return
        if value == "cxl:no":
            await self._show_booking_detail(wa_id, phone, customer_id, data.get("booking_id", ""))
            return
        try:
            await self.booking_service.cancel_booking(
                data["booking_id"], BookingCancelRequest(reason="Cancelled by customer via WhatsApp"),
                actor_id=customer_id, actor_role="customer",
            )
        except AppException as exc:
            await self.wa.send_text(phone, f"Couldn't cancel: {exc.message}")
            await self._send_menu(wa_id, phone)
            return
        await self._set_state(wa_id, None, {})
        await self.wa.send_text(phone, "✅ Your booking has been cancelled. Type *hi* any time to book again.")

    # -- vehicles: type → service, single booking ------------------------
    #
    # Quick-booking model (2026-09): no saved vehicles, no plate, no
    # brand/model. WhatsApp takes one vehicle per booking, deliberately:
    # the web app/manager portal can still create multi-vehicle visits.

    @staticmethod
    def _variant_count(service: dict) -> int:
        m = re.search(r"\d+", service.get("variant_label") or "")
        return int(m.group()) if m else 1

    @staticmethod
    def _price_for(service: dict, vehicle_type: str) -> float:
        return float((service.get("vehicle_type_prices") or {}).get(vehicle_type, service.get("price") or 0))

    @staticmethod
    def _display_name(service: dict) -> str:
        name = service.get("name") or "Service"
        return name.split("(")[0].strip() if service.get("variant_group") else name

    async def _start_type_step(self, wa_id, phone, customer_id, data):
        data.pop("strikes", None)
        data.pop("cur", None)
        types = await self.vehicle_types.find_all_no_paginate({"is_active": True}, sort_by="display_order", sort_order=1)
        rows = [{"id": f"vt:{t['_id']}", "title": _short(t.get("name", "Type"))} for t in types[:10]]
        prompt = "What are we washing? Pick the vehicle type:"
        await self.wa.send_list(phone, prompt, "Vehicle type", rows)
        await self._set_state(wa_id, "q_type", data)

    async def _on_type(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("vt:"):
            await self._nudge(wa_id, phone, "q_type", data, "Please pick the vehicle type from the list above 👆", str(value))
            return
        vt = await self.vehicle_types.find_by_id(value.split(":", 1)[1])
        if not vt or not vt.get("is_active", True):
            await self.wa.send_text(phone, "That type isn't available — pick again from the list.")
            return
        name = vt.get("name") or "vehicle"
        data["cur"] = {"vehicle_type": str(vt["_id"]), "type_name": name, "count": 1}
        await self._start_service_step(wa_id, phone, data)

    async def _start_service_step(self, wa_id, phone, data):
        data.pop("strikes", None)
        cur = data.get("cur") or {}
        vt = cur.get("vehicle_type")
        services = await self.services.find_all_no_paginate({"is_active": True})
        eligible = [s for s in services if not s.get("is_addon") and (not s.get("vehicle_types") or vt in s["vehicle_types"])]
        # One row per product: bike-wash variants ("1 bike"… "4 bikes")
        # collapse to the smallest — the count already asked decides the rest.
        groups: dict[str, dict] = {}
        for s in eligible:
            key = s.get("variant_group") or str(s["_id"])
            if key not in groups or self._variant_count(s) < self._variant_count(groups[key]):
                groups[key] = s
        options = list(groups.values())[:10]
        if not options:
            await self.wa.send_text(phone, "Sorry — no services are available for this vehicle type right now. Type *hi* to start over.")
            await self._set_state(wa_id, None, {})
            return
        count = int(cur.get("count") or 1)
        rows = []
        for s in options:
            name = self._display_name(s)
            price = self._price_for(s, vt)
            rows.append({
                "id": f"svc:{s['_id']}",
                "title": _short(name),
                "description": _short(f"₹{price:g}", 72),
            })
        who = f"your {cur.get('type_name', 'vehicle')}"
        await self.wa.send_list(phone, f"Which service for {who}?", "Choose service", rows)
        await self._set_state(wa_id, "q_service", data)

    async def _build_line(self, cur: dict, service: dict) -> dict:
        """A booking line from a type, a count and the chosen service —
        mirrors the website's QuickBookFlow line maths."""
        vt = cur["vehicle_type"]
        count = int(cur.get("count") or 1)
        is_bike = bool(BookingService._BIKE_WORD.search(cur.get("type_name") or ""))
        service_ids = [str(service["_id"])]
        quantities: dict[str, int] = {}
        subtotal = self._price_for(service, vt)
        name = service.get("name") or "Service"
        quantity = count
        if is_bike and count > 1:
            quantity = 1
            exact = None
            group = service.get("variant_group")
            if group:
                for s in await self.services.find_all_no_paginate({"is_active": True, "variant_group": group}):
                    if self._variant_count(s) == count:
                        exact = s
            if exact:
                service_ids = [str(exact["_id"])]
                subtotal = self._price_for(exact, vt)
                name = exact.get("name") or name
            else:
                addons = await self.services.find_all_no_paginate({"is_active": True, "is_addon": True})
                add_bike = next((a for a in addons if BookingService._BIKE_WORD.search(a.get("name", "")) and "polish" not in a.get("name", "").lower()), None)
                extra = count - self._variant_count(service)
                if add_bike and extra > 0:
                    service_ids.append(str(add_bike["_id"]))
                    quantities[str(add_bike["_id"])] = extra
                    subtotal += self._price_for(add_bike, vt) * extra
                elif not add_bike:
                    quantity = count  # no per-bike line in the catalogue: N separate bookings
        return {
            "vehicle_type": vt,
            "type_name": cur.get("type_name") or "Vehicle",
            "count": count,
            "quantity": quantity,
            "service_ids": service_ids,
            "service_quantities": quantities,
            "service_name": self._display_name({"name": name, "variant_group": service.get("variant_group")}) if quantity > 1 else name,
            "subtotal": round(subtotal * quantity, 2),
        }

    @staticmethod
    def _lines_summary(lines: list[dict]) -> str:
        out = []
        for line in lines:
            count = int(line.get("count") or 1)
            prefix = f"{count} × " if count > 1 else ""
            out.append(f"• {prefix}{line['type_name']} · {line['service_name']} — ₹{line['subtotal']:g}")
        return "\n".join(out)

    async def _on_service(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("svc:"):
            await self._nudge(wa_id, phone, "q_service", data, "Please pick a service from the list above 👆", str(value))
            return
        service = await self.services.find_by_id(value.split(":", 1)[1])
        if not service or not service.get("is_active"):
            await self.wa.send_text(phone, "That service isn't available any more — pick another from the list.")
            return
        cur = data.get("cur") or {}
        if not cur.get("vehicle_type"):
            await self._start_type_step(wa_id, phone, customer_id, data)
            return
        line = await self._build_line(cur, service)
        data["lines"] = [line]
        data.pop("cur", None)
        await self._start_when_step(wa_id, phone, data)

    # -- address / live location --------------------------------------

    async def _start_address_step(self, wa_id, phone, customer_id, data):
        data.pop("strikes", None)
        addresses = await self.addresses.find_all_no_paginate({"owner_id": customer_id})
        if not addresses:
            await self._request_location(wa_id, phone, data)
            return
        rows = [
            {"id": f"addr:{a['_id']}", "title": _short(a.get("label", "Address")), "description": _short(a.get("line1", ""), 72)}
            for a in addresses[:9]
        ]
        rows.append({"id": "addr:new", "title": "📍 Share a new location"})
        await self.wa.send_list(phone, "Where should the captain come?", "Choose address", rows)
        await self._set_state(wa_id, "q_address", data)

    async def _on_address(self, wa_id, phone, customer_id, data, kind, value):
        if kind == "location":
            # Customer skipped the list and just shared a pin — accept it.
            await self._on_await_location(wa_id, phone, customer_id, data, kind, value)
            return
        if kind != "reply" or not value.startswith("addr:"):
            await self._nudge(wa_id, phone, "q_address", data, "Please pick an address from the list above 👆", str(value))
            return
        if value == "addr:new":
            await self._request_location(wa_id, phone, data)
            return
        address_id = value.split(":", 1)[1]
        address = await self.addresses.find_by_id(address_id)
        if not address or address.get("owner_id") != customer_id:
            await self.wa.send_text(phone, "That address isn't on your account — pick again from the list.")
            return
        data["address_id"] = address_id
        data["address_label"] = _short(address.get("line1") or address.get("label") or "your address", 60)
        if not await self._resolve_center_for(wa_id, phone, data, address):
            return
        await self._hold_and_confirm(wa_id, phone, customer_id, data)

    async def _request_location(self, wa_id, phone, data):
        await self.wa.send_location_request(phone, "Please share your location 📍 so we can send the captain to your doorstep.")
        await self._set_state(wa_id, "await_location", data)

    async def _on_await_location(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "location" or value[0] is None:
            await self.wa.send_text(phone, "Tap the *Send location* button above and share your pin 📍 (or type *cancel* to stop).")
            return
        data["latitude"], data["longitude"] = float(value[0]), float(value[1])
        data["addr_line1"] = "Shared WhatsApp location"
        probe = {"latitude": data.get("latitude"), "longitude": data.get("longitude")}
        try:
            center, _ = await self.booking_service._resolve_service_center(probe)
        except AppException:
            await self.wa.send_text(phone, "Please share the area pincode too (6 digits).")
            await self._set_state(wa_id, "await_addr_pincode", data)
            return
        await self._save_new_address(wa_id, phone, customer_id, data, center, "")

    async def _on_await_addr_pincode(self, wa_id, phone, customer_id, data, kind, value):
        pincode = str(value).strip() if kind == "text" else ""
        if not (pincode.isdigit() and 4 <= len(pincode) <= 10):
            await self.wa.send_text(phone, "That doesn't look like a pincode — please type the 6-digit area pincode.")
            return
        probe = {"latitude": data.get("latitude"), "longitude": data.get("longitude"), "pincode": pincode}
        try:
            center, _ = await self.booking_service._resolve_service_center(probe)
        except AppException:
            await self.wa.send_text(phone, "Sorry — doorstep service isn't available in your area yet. 😔 Type *hi* to start over.")
            await self._set_state(wa_id, None, {})
            return
        await self._save_new_address(wa_id, phone, customer_id, data, center, pincode)

    async def _save_new_address(self, wa_id, phone, customer_id, data, center: dict, pincode: str) -> None:
        location = center.get("location") or {}
        address = await self.address_service.create(
            customer_id,
            AddressCreateRequest(
                label="WhatsApp",
                line1=data.get("addr_line1") or "Shared location",
                city=location.get("city") or "—",
                state=location.get("state") or "—",
                pincode=pincode or str(location.get("pincode") or "000000"),
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                is_default=True,
            ),
        )
        data["address_id"] = address["id"]
        data["address_label"] = _short(data.get("addr_line1") or "your location", 60)
        data["center_id"] = str(center["_id"])
        data["center_name"] = center.get("name")
        for key in ("addr_line1", "latitude", "longitude"):
            data.pop(key, None)
        await self._hold_and_confirm(wa_id, phone, customer_id, data)

    async def _resolve_center_for(self, wa_id, phone, data, address: dict) -> bool:
        try:
            center, _ = await self.booking_service._resolve_service_center(address, allow_pinless=True)
        except AppException:
            await self.wa.send_text(phone, "Sorry — doorstep service isn't available at that address yet. 😔 Type *hi* to start over.")
            await self._set_state(wa_id, None, {})
            return False
        data["center_id"] = str(center["_id"])
        data["center_name"] = center.get("name")
        return True

    # -- when: day + slot in ONE pick -----------------------------------

    async def _slot_center_id(self, data: dict) -> str | None:
        if data.get("center_id"):
            return data["center_id"]
        centers = await self.booking_service.center_repo.find_all_no_paginate({"is_active": True}, sort_by="created_at", sort_order=1)
        return str(centers[0]["_id"]) if centers else None

    async def _start_when_step(self, wa_id, phone, data):
        data.pop("strikes", None)
        # THE centralization point: identical availability to the website —
        # per-slot counters + admin capacity policy + closures + cutoffs
        # all come from the same BookingService.available_slots. In the
        # new short WhatsApp flow this comes before location, so use the
        # active/default center for the early picker and revalidate after
        # location resolves the actual service center.
        center_id = await self._slot_center_id(data)
        if not center_id:
            await self.wa.send_text(phone, "Please share your location first so we can show available times.")
            await self._request_location(wa_id, phone, data)
            return
        data["slot_center_id"] = center_id
        today = now_ist().date()
        rows: list[dict] = []
        for i in range(3):
            d = today + timedelta(days=i)
            label = "Today" if i == 0 else "Tomorrow" if i == 1 else d.strftime("%a %d %b")
            try:
                slots = await self.booking_service.available_slots(center_id, d.isoformat())
            except AppException:
                continue
            for s in slots:
                if s["status"] == "full":
                    continue
                rows.append({
                    "id": f"when:{d.isoformat()}|{s['key']}",
                    "title": _short(f"{label} · {s['start']}–{s['end']}"),
                    "description": "Available" if s["status"] == "available" else f"Only {s['remaining']} spot(s) left",
                })
                if len(rows) >= 10:
                    break
            if len(rows) >= 10:
                break
        if not rows:
            await self.wa.send_text(phone, "Everything's booked for the next 3 days 😔 — please try again tomorrow, or type *hi* for the menu.")
            await self._set_state(wa_id, None, {})
            return
        await self.wa.send_list(phone, "When should we come?", "Pick a time", rows)
        await self._set_state(wa_id, "q_when", data)

    async def _on_when(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not str(value).startswith("when:") or "|" not in str(value):
            await self._nudge(wa_id, phone, "q_when", data, "Please pick a time from the list above 👆", str(value))
            return
        date_str, slot = str(value).split(":", 1)[1].split("|", 1)
        data["date"], data["slot"] = date_str, slot
        await self._start_address_step(wa_id, phone, customer_id, data)

    async def _hold_and_confirm(self, wa_id, phone, customer_id, data):
        if not data.get("center_id") or not data.get("date") or not data.get("slot"):
            await self._start_when_step(wa_id, phone, data)
            return
        # Theater-seat hold: claim the seat NOW so nobody else can take it
        # while this customer reads the confirmation message. Expiry frees
        # it automatically if they walk away.
        try:
            await self.booking_service.hold_slot(customer_id, data["center_id"], data["date"], data["slot"])
        except AppException as exc:
            await self.wa.send_text(phone, f"{exc.message}")
            await self._start_when_step(wa_id, phone, data)
            return
        lines = data.get("lines") or []
        total = round(sum(float(l.get("subtotal") or 0) for l in lines), 2)
        summary = (
            "Confirm your booking:\n\n"
            f"{self._lines_summary(lines)}\n\n"
            f"📍 {data.get('address_label') or 'your address'}\n"
            f"📅 {data.get('date')} · {data.get('slot')}\n"
            f"💰 ₹{total:g}"
        )
        await self.wa.send_buttons(phone, summary, [{"id": "confirm:yes", "title": "✅ Confirm"}, {"id": "confirm:no", "title": "❌ Cancel"}])
        await self._set_state(wa_id, "confirm", data)

    # -- book again -----------------------------------------------------

    async def _last_visit(self, customer_id: str) -> list[dict]:
        """The customer's most recent non-cancelled booking and its visit
        siblings — what "Book again" replays."""
        last = await self.bookings.collection.find_one(
            {"customer_id": customer_id, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"}},
            sort=[("created_at", -1)],
        )
        if not last:
            return []
        return await self.booking_service._visit_cars(last)

    async def _book_again(self, wa_id, phone, customer_id, data):
        cars = await self._last_visit(customer_id)
        if not cars:
            await self._start_type_step(wa_id, phone, customer_id, {"lines": []})
            return
        # WhatsApp bookings stay single-vehicle. If the last website/staff
        # visit had several vehicles, replay just the first one here.
        cars = cars[:1]
        types = {str(t["_id"]): t.get("name") for t in await self.vehicle_types.find_all_no_paginate({})}
        merged: dict[tuple, dict] = {}
        for c in cars:
            vt = c.get("vehicle_type")
            if not vt and c.get("vehicle_id"):
                v = await self.vehicles.find_by_id(c["vehicle_id"])
                vt = (v or {}).get("vehicle_type")
            if not vt:
                continue
            service_ids = list(c.get("service_ids") or [])
            quantities = dict(c.get("service_quantities") or {})
            key = (vt, tuple(sorted(service_ids)), tuple(sorted(quantities.items())))
            services = [await self.services.find_by_id(sid) for sid in service_ids]
            services = [s for s in services if s and s.get("is_active")]
            if not services or not any(not s.get("is_addon") for s in services):
                continue
            base = next(s for s in services if not s.get("is_addon"))
            is_bike = bool(BookingService._BIKE_WORD.search(types.get(vt) or ""))
            bikes = self._variant_count(base) + sum(int(q) for sid, q in quantities.items() if any(str(s["_id"]) == sid and BookingService._BIKE_WORD.search(s.get("name", "")) and "polish" not in s.get("name", "").lower() for s in services))
            subtotal = sum(self._price_for(s, vt) * int(quantities.get(str(s["_id"]), 1)) for s in services)
            if key in merged:
                merged[key]["quantity"] += 1
                merged[key]["count"] += 1
                merged[key]["subtotal"] = round(merged[key]["subtotal"] + subtotal, 2)
            else:
                merged[key] = {
                    "vehicle_type": vt,
                    "type_name": types.get(vt) or c.get("vehicle_label") or "Vehicle",
                    "count": bikes if is_bike else 1,
                    "quantity": 1,
                    "service_ids": service_ids,
                    "service_quantities": quantities,
                    "service_name": self._display_name(base) if is_bike else base.get("name"),
                    "subtotal": round(subtotal, 2),
                }
        lines = list(merged.values())
        if not lines:
            await self._start_type_step(wa_id, phone, customer_id, {"lines": []})
            return
        data["lines"] = lines
        address = await self.addresses.find_by_id(cars[0].get("address_id") or "")
        if not address or address.get("owner_id") != customer_id:
            await self._start_address_step(wa_id, phone, customer_id, data)
            return
        data["address_id"] = str(address["_id"])
        data["address_label"] = _short(address.get("line1") or address.get("label") or "your address", 60)
        if not await self._resolve_center_for(wa_id, phone, data, address):
            return
        await self.wa.send_text(phone, f"Same as last time 👍\n\n{self._lines_summary(lines)}\n📍 {data['address_label']}")
        await self._start_when_step(wa_id, phone, data)

    # -- payment choice (stateless — booking id rides in the button id) --

    async def _on_pay_choice(self, phone: str, customer_id: str, value: str) -> None:
        try:
            _, choice, booking_id = value.split(":", 2)
        except ValueError:
            return
        booking = await self.booking_service.repo.find_by_id(booking_id)
        if not booking or booking.get("customer_id") != customer_id:
            await self.wa.send_text(phone, "Hmm, I couldn't find that booking. Type *hi* for the menu.")
            return
        if booking.get("payment_status") == "paid":
            await self.wa.send_text(phone, f"Booking *{booking['booking_number']}* is already paid ✅")
            return
        if booking.get("status") == "cancelled":
            await self.wa.send_text(phone, f"Booking *{booking['booking_number']}* was cancelled — nothing to pay.")
            return

        # A multi-vehicle visit is paid as ONE: amount and settlement cover
        # every car on it.
        cars = await self.booking_service._visit_cars(booking)
        visit_total = round(sum(float(c.get("total_amount") or 0) for c in cars), 2)
        if choice == "cash":
            if booking.get("status") == "awaiting_payment":
                # This booking isn't real yet — nothing was dispatched, no
                # one was told. Choosing cash here is the actual promotion
                # step (same one the web app's "Pay cash instead" button
                # triggers): it confirms the booking, enters the manager's
                # queue, and sends the normal booking-confirmed message.
                try:
                    # notify_background=False — the very next line sends
                    # this thread's own confirmation text, which must not
                    # race the announcement's WhatsApp send.
                    if booking.get("booking_group_id"):
                        await self.booking_service.switch_group_to_cash(booking["booking_group_id"], customer_id, notify_background=False)
                    else:
                        await self.booking_service.switch_to_cash(booking_id, customer_id, notify_background=False)
                except AppException as exc:
                    await self.wa.send_text(phone, f"Couldn't switch this to cash: {exc.message}")
                    return
                await self.wa.send_text(
                    phone,
                    f"Booking *{booking['booking_number']}* is confirmed ✅ — pay the captain "
                    f"*₹{visit_total:g}* in cash after the wash.",
                )
                return
            await self.wa.send_text(
                phone,
                f"Noted 💵 — pay the captain *₹{visit_total:g}* in cash after the wash. "
                "Changed your mind? Just tap *Pay online now* above any time before the service.",
            )
            return

        # Online: flip the booking to the online method (so the web app's
        # "Pay online" button also works for it) and drop a Razorpay
        # Payment Link right here in the chat. The link is marked paid by
        # the signature-verified browser callback or the reminder-loop
        # sweep — whichever lands first; the chat gets a ✅ then.
        from app.services.payment_service import PaymentService

        try:
            for car in cars:
                if car.get("payment_method") == "cash":
                    await self.booking_service.repo.update_by_id(str(car["_id"]), {"payment_method": "online"})
            unpaid = [c for c in cars if c.get("payment_status") != "paid"]
            link = await PaymentService(self.db).create_payment_link(
                booking, contact_phone=phone, name=booking.get("customer_name"), cars=unpaid or None
            )
        except AppException as exc:
            await self.wa.send_text(phone, f"Couldn't set up the payment: {exc.message}")
            return
        await self.wa.send_text(
            phone,
            f"Here's your secure payment link for *₹{visit_total:g}* "
            f"(UPI, cards, netbanking — powered by Razorpay):\n\n{link['short_url']}\n\n"
            "I'll confirm right here the moment it's received ✅",
        )

    async def send_payment_reminder(self, booking: dict) -> bool:
        """A proactive "still hasn't paid" nudge with the SAME tappable
        buttons _on_confirm sends right after booking — called from the
        reminder sweep (main.py), not the webhook dispatcher. Stateless by
        design: whichever button gets tapped, whenever, is handled by the
        "pay:" intercept above exactly as if it just arrived — including
        promoting an AWAITING_PAYMENT booking to real (see the cash branch
        above), so this is safe to send for a booking still waiting to be
        confirmed, not just an already-real one that's merely unpaid.

        This is a plain interactive message, not an approved template —
        it only actually reaches the customer within WhatsApp's 24h
        session window. The caller (find_bookings_payment_reminder_due's
        sweep) already sends the approved wa_event="payment_pending"
        template alongside this, which is the one guaranteed to land
        outside that window; this is the nicer, tappable version for
        whenever the window is open. Best-effort — a failed send here
        must never be treated as the reminder itself having failed."""
        phone = booking.get("customer_phone")
        if not phone or float(booking.get("total_amount") or 0) < 1:
            return False
        amount = booking.get("total_amount")
        booking_id = str(booking.get("_id") or booking.get("id"))
        return await self.wa.send_buttons(
            phone,
            f"⏳ *{booking.get('booking_number')}* is still waiting on payment — *₹{amount}*.\n"
            "Pay online now, or choose cash and we'll confirm it straight away.",
            [
                {"id": f"pay:online:{booking_id}", "title": "💳 Pay online now"},
                {"id": f"pay:cash:{booking_id}", "title": "💵 Cash after wash"},
            ],
        )

    # -- confirm & create ---------------------------------------------

    async def _on_confirm(self, wa_id, phone, customer_id, data, kind, value):
        if kind != "reply" or not value.startswith("confirm:"):
            await self._nudge(wa_id, phone, "confirm", data, "Tap *Confirm* or *Cancel* above 👆", str(value))
            return
        if value == "confirm:no":
            # Give the held seat back immediately instead of waiting for expiry.
            if data.get("center_id") and data.get("date") and data.get("slot"):
                await self.booking_service.release_hold(customer_id, data["center_id"], data["date"], data["slot"])
            await self._set_state(wa_id, None, {})
            await self.wa.send_text(phone, "No problem — nothing was booked. Type *hi* any time.")
            return
        customer = await self.users.find_by_id(customer_id)
        lines = data.get("lines") or []
        if not customer or not lines or not data.get("address_id") or not data.get("date") or not data.get("slot"):
            await self.wa.send_text(phone, "Hmm, that booking got lost along the way — let's start again. Type *hi*.")
            await self._set_state(wa_id, None, {})
            return
        try:
            result = await self.booking_service.create_quick_booking(
                QuickBookingRequest(
                    customer_name=(customer.get("full_name") or "Customer").strip() or "Customer",
                    customer_phone=customer.get("phone") or phone,
                    address_id=data["address_id"],
                    lines=[
                        QuickBookingLine(
                            vehicle_type=l["vehicle_type"],
                            quantity=int(l.get("quantity") or 1),
                            service_ids=list(l.get("service_ids") or []),
                            service_quantities={k: int(v) for k, v in (l.get("service_quantities") or {}).items()},
                        )
                        for l in lines
                    ],
                    scheduled_date=data["date"],
                    scheduled_slot=data["slot"],
                    customer_notes="Booked via WhatsApp",
                ),
                customer=customer,
                # The bot confirms first and offers a pay link right after —
                # so the visit is created REAL (cash), never parked
                # awaiting payment (source != "app"), and the pin the
                # customer shared is trusted (allow_pinless for the rare
                # typed-only address).
                source="whatsapp",
                allow_pinless=True,
                # The bot's own "confirmed" text follows immediately — the
                # announcement's WhatsApp send must finish first.
                notify_background=False,
            )
        except AppException as exc:
            # Most common real cause: the slot filled up (web + WhatsApp
            # draw from the same capacity pool) between listing and
            # confirming — offer a fresh pick rather than dead-ending.
            await self.wa.send_text(phone, f"Couldn't complete the booking: {exc.message}")
            await self._start_when_step(wa_id, phone, data)
            return
        await self._set_state(wa_id, None, {})
        numbers = " + ".join(result.get("booking_numbers") or [])
        total = float(result.get("total_amount") or 0)
        code = result.get("service_code")
        await self.wa.send_text(
            phone,
            "🎉 Booking confirmed!\n\n"
            f"🧾 *{numbers}*\n"
            f"{self._lines_summary(lines)}\n"
            f"📅 {data.get('date')} · {data.get('slot')}\n"
            f"💰 Total: ₹{total:g}\n\n"
            + (f"🔐 Your service code: *{code}*\nShare it with the captain when they arrive.\n\n" if code else "")
            + "Our captain's details will be shared here once assigned.",
        )
        # Founder rule: every service booking takes cash OR online. The
        # buttons are stateless (booking id rides in the button id), so
        # the customer can tap them any time later too — see the "pay:"
        # intercept in the dispatcher. One button pair for the whole visit.
        first = (result.get("bookings") or [{}])[0]
        if total >= 1 and first.get("id"):
            await self.wa.send_buttons(
                phone,
                "How would you like to pay?",
                [
                    {"id": f"pay:online:{first['id']}", "title": "💳 Pay online now"},
                    {"id": f"pay:cash:{first['id']}", "title": "💵 Cash after wash"},
                ],
            )
