"""
WhatsApp CRM behind the admin panel's WhatsApp section.

Storage model — deliberately built on what already exists:
  - OUTBOUND messages are the existing `whatsapp_outbox` rows (every
    provider send already lands there with wamid + delivery_status from
    the statuses webhook). The CRM reads them as the outgoing half of a
    thread — one write path, no double bookkeeping.
  - INBOUND messages are recorded in the new `whatsapp_inbox` collection
    by the webhook (all types: text, media, location, interactive) —
    recording happens BEFORE and independently of the booking bot's
    state machine, so an agent conversation is never lost to a bot error.
  - `whatsapp_conversations` (the bot's state doc, one per wa_id) gains
    the CRM fields: unread_count, crm_status, assigned_to, tags,
    last_message_*, bot_paused. The bot checks bot_paused so a human
    agent and the bot never talk over each other.

Sensitive traffic (OTPs, temp passwords — outbox rows tagged kind otp/
temp_password) is shown in threads only as a redacted placeholder.

24-hour window: Meta only delivers free-form messages while the customer
has messaged us within 24h. `window` on a conversation is computed from
the last inbound timestamp and ENFORCED on the send endpoint, not just
hinted in the UI.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.services.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

CRM_STATUSES = ("open", "pending", "resolved")
DEFAULT_TAGS = [
    "New Customer", "Repeat Customer", "VIP", "Booking Issue", "Complaint",
    "Payment Issue", "Reschedule", "Feedback", "Follow-up Required",
]

# Booking events -> the dedicated template that should carry them once
# approved (see bootstrap_blussit_templates). Fallback is always the
# generic update template via send_generic.
EVENT_TEMPLATES = {
    "welcome": "blussit_account_created",
    "booking_confirmed": "blussit_booking_confirmed",
    "captain_assigned": "blussit_captain_assigned",
    "captain_on_the_way": "blussit_captain_on_the_way",
    "service_completed": "blussit_service_completed",
    "review_request": "blussit_review_request",
    "booking_reminder": "blussit_booking_reminder",
    "reschedule_confirmation": "blussit_reschedule_confirmation",
    "payment_confirmation": "blussit_payment_confirmation",
}

WINDOW_HOURS = 24


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    a = _aware(dt)
    return a.isoformat() if a else None


def _local_phone(wa_id: str) -> str:
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    return digits


def _wa_id_variants(phone_or_wa_id: str) -> list[str]:
    """The outbox stores whatever the caller passed (usually the bare
    10-digit form); inbox stores the wa_id. Match both."""
    digits = "".join(ch for ch in phone_or_wa_id if ch.isdigit())
    variants = {digits}
    if len(digits) == 10:
        variants.add(f"91{digits}")
    if len(digits) == 12 and digits.startswith("91"):
        variants.add(digits[2:])
    return list(variants)


def _summarize_inbound(message: dict) -> tuple[str, str, dict]:
    """Returns (message_type, display_text, extra fields)."""
    mtype = message.get("type") or "unknown"
    if mtype == "text":
        return "text", (message.get("text") or {}).get("body", ""), {}
    if mtype in ("image", "video", "audio", "document", "sticker"):
        media = message.get(mtype) or {}
        extra = {
            "media_id": media.get("id"),
            "mime_type": media.get("mime_type"),
            "filename": media.get("filename"),
            "caption": media.get("caption"),
        }
        label = {"image": "📷 Photo", "video": "🎬 Video", "audio": "🎙 Audio", "document": f"📄 {media.get('filename') or 'Document'}", "sticker": "Sticker"}[mtype]
        return mtype, media.get("caption") or label, extra
    if mtype == "location":
        loc = message.get("location") or {}
        return "location", "📍 Location shared", {"latitude": loc.get("latitude"), "longitude": loc.get("longitude")}
    if mtype == "interactive":
        inter = message.get("interactive") or {}
        reply = inter.get("list_reply") or inter.get("button_reply") or {}
        return "interactive", reply.get("title") or reply.get("id") or "Selection", {}
    if mtype == "button":
        return "interactive", (message.get("button") or {}).get("text", "Button reply"), {}
    return mtype, f"[{mtype}]", {}


class WhatsAppCrmService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.wa = WhatsAppService(db)

    # ------------------------------------------------------------------
    # Recording (called from the webhook path — must never raise)
    # ------------------------------------------------------------------

    async def record_inbound(self, wa_id: str, profile_name: str | None, message: dict) -> None:
        try:
            mtype, text, extra = _summarize_inbound(message)
            now = datetime.now(timezone.utc)
            await self.db.whatsapp_inbox.insert_one({
                "wamid": message.get("id"),
                "wa_id": wa_id,
                "phone": _local_phone(wa_id),
                "message_type": mtype,
                "text": text,
                **{k: v for k, v in extra.items() if v is not None},
                "profile_name": profile_name,
                "created_at": now,
            })
            existing = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id})
            update = {
                "$set": {
                    "phone": _local_phone(wa_id),
                    "last_message_text": text[:200],
                    "last_message_at": now,
                    "last_message_direction": "in",
                    "last_inbound_at": now,
                    **({"profile_name": profile_name} if profile_name else {}),
                    # A resolved conversation that gets a new customer
                    # message is a conversation again.
                    **({"crm_status": "open"} if not existing or existing.get("crm_status") in (None, "resolved") else {}),
                },
                "$inc": {"unread_count": 1},
                "$setOnInsert": {"wa_id": wa_id, "created_at": now, "tags": [], "bot_paused": False},
            }
            await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, update, upsert=True)
            if not existing:
                await self._notify_admins("New WhatsApp conversation", f"{profile_name or _local_phone(wa_id)} messaged for the first time: {text[:80]}")
        except Exception:  # noqa: BLE001 — never break webhook processing
            logger.exception("Failed recording inbound WhatsApp message from %s", wa_id)

    async def _notify_admins(self, title: str, message: str) -> None:
        try:
            from app.models.enums import NotificationType
            from app.services.notification_service import NotificationService

            notifications = NotificationService(self.db)
            async for admin in self.db.users.find({"role": "admin", "is_deleted": {"$ne": True}}).limit(3):
                await notifications.notify(str(admin["_id"]), title, message, NotificationType.SYSTEM)
        except Exception:  # noqa: BLE001
            logger.exception("Failed notifying admins for WhatsApp CRM event")

    async def is_bot_paused(self, wa_id: str) -> bool:
        convo = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id}, {"bot_paused": 1})
        return bool(convo and convo.get("bot_paused"))

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def _window_state(self, convo: dict) -> dict:
        last_in = _aware(convo.get("last_inbound_at"))
        if not last_in:
            return {"active": False, "expires_at": None}
        expires = last_in + timedelta(hours=WINDOW_HOURS)
        return {"active": datetime.now(timezone.utc) < expires, "expires_at": expires.isoformat()}

    async def _convo_out(self, convo: dict, names: dict[str, str] | None = None) -> dict:
        customer_id = convo.get("customer_id")
        return {
            "wa_id": convo["wa_id"],
            "phone": convo.get("phone") or _local_phone(convo["wa_id"]),
            "name": (names or {}).get(customer_id) or convo.get("profile_name") or _local_phone(convo["wa_id"]),
            "customer_id": customer_id,
            "last_message_text": convo.get("last_message_text"),
            "last_message_at": _iso(convo.get("last_message_at")),
            "last_message_direction": convo.get("last_message_direction"),
            "unread_count": convo.get("unread_count", 0),
            "crm_status": convo.get("crm_status", "open"),
            "assigned_to": convo.get("assigned_to"),
            "assigned_to_name": convo.get("assigned_to_name"),
            "tags": convo.get("tags", []),
            "bot_paused": convo.get("bot_paused", False),
            "window": self._window_state(convo),
            "has_active_booking": convo.get("_has_active_booking", False),
        }

    async def list_conversations(self, filter_key: str = "all", search: str = "", agent_id: str | None = None) -> list[dict]:
        query: dict = {}
        if filter_key == "unread":
            query["unread_count"] = {"$gt": 0}
        elif filter_key in CRM_STATUSES:
            query["crm_status"] = filter_key if filter_key != "open" else {"$in": ["open", None]}
        elif filter_key == "mine" and agent_id:
            query["assigned_to"] = agent_id
        elif filter_key == "complaint":
            query["tags"] = {"$in": ["Complaint", "Booking Issue", "Payment Issue"]}

        if search:
            ids = await self._search_wa_ids(search)
            if ids is not None:
                query["wa_id"] = {"$in": ids}

        rows = await self.db.whatsapp_conversations.find(query).sort("last_message_at", -1).limit(100).to_list(length=None)
        rows = [r for r in rows if r.get("last_message_at")]  # bot-state-only docs without traffic

        customer_ids = [ObjectId(r["customer_id"]) for r in rows if r.get("customer_id") and ObjectId.is_valid(r["customer_id"])]
        users = {str(u["_id"]): u for u in await self.db.users.find({"_id": {"$in": customer_ids}}).to_list(length=None)} if customer_ids else {}
        names = {uid: u.get("full_name", "") for uid, u in users.items()}

        # Booking indicator + the two customer-derived filters.
        active_by_customer: set[str] = set()
        if rows:
            cids = [r["customer_id"] for r in rows if r.get("customer_id")]
            if cids:
                async for b in self.db.bookings.aggregate([
                    {"$match": {"customer_id": {"$in": cids}, "status": {"$in": ["pending", "assigned", "on_the_way", "in_progress", "rescheduled"]}, "is_deleted": {"$ne": True}}},
                    {"$group": {"_id": "$customer_id"}},
                ]):
                    active_by_customer.add(b["_id"])
        out = []
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        for r in rows:
            r["_has_active_booking"] = r.get("customer_id") in active_by_customer
            if filter_key == "booking" and not r["_has_active_booking"]:
                continue
            if filter_key == "new_customer":
                u = users.get(r.get("customer_id") or "")
                if not u or _aware(u.get("created_at", week_ago)) < week_ago:
                    continue
            out.append(await self._convo_out(r, names))
        return out

    async def _search_wa_ids(self, search: str) -> list[str] | None:
        """Resolves a free-text search (name / phone / booking no / vehicle
        reg) to the matching conversation wa_ids."""
        s = search.strip()
        wa_ids: set[str] = set()
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 4:
            async for c in self.db.whatsapp_conversations.find({"$or": [{"wa_id": {"$regex": digits}}, {"phone": {"$regex": digits}}]}, {"wa_id": 1}):
                wa_ids.add(c["wa_id"])

        phones: set[str] = set()
        # Booking number
        if s.upper().startswith("BK"):
            booking = await self.db.bookings.find_one({"booking_number": s.upper()})
            if booking:
                cust = await self.db.users.find_one({"_id": ObjectId(booking["customer_id"])}) if ObjectId.is_valid(booking["customer_id"]) else None
                if cust and cust.get("phone"):
                    phones.add(cust["phone"])
        # Vehicle registration
        plate = "".join(ch for ch in s.upper() if ch.isalnum())
        if len(plate) >= 6:
            vehicle = await self.db.vehicles.find_one({"registration_number": {"$regex": f"^{re.escape(plate)}$", "$options": "i"}})
            if vehicle:
                owner = await self.db.users.find_one({"_id": ObjectId(vehicle["owner_id"])}) if ObjectId.is_valid(vehicle.get("owner_id", "")) else None
                if owner and owner.get("phone"):
                    phones.add(owner["phone"])
        # Customer name
        async for u in self.db.users.find({"full_name": {"$regex": re.escape(s), "$options": "i"}, "role": "customer"}, {"phone": 1}).limit(20):
            if u.get("phone"):
                phones.add(u["phone"])

        for phone in phones:
            for v in _wa_id_variants(phone):
                async for c in self.db.whatsapp_conversations.find({"$or": [{"wa_id": v}, {"phone": v}]}, {"wa_id": 1}):
                    wa_ids.add(c["wa_id"])
        return list(wa_ids)

    # ------------------------------------------------------------------
    # Thread
    # ------------------------------------------------------------------

    async def get_thread(self, wa_id: str, limit: int = 100) -> dict:
        convo = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id})
        if not convo:
            raise NotFoundException("Conversation not found")
        phones = _wa_id_variants(wa_id)

        inbound = await self.db.whatsapp_inbox.find({"wa_id": wa_id}).sort("created_at", -1).limit(limit).to_list(length=None)
        outbound = await self.db.whatsapp_outbox.find({"phone": {"$in": phones}}).sort("created_at", -1).limit(limit).to_list(length=None)

        agent_ids = [ObjectId(o["agent_id"]) for o in outbound if o.get("agent_id") and ObjectId.is_valid(o["agent_id"])]
        agents = {str(u["_id"]): u.get("full_name", "Agent") for u in await self.db.users.find({"_id": {"$in": agent_ids}}).to_list(length=None)} if agent_ids else {}

        messages = []
        for m in inbound:
            messages.append({
                "id": str(m["_id"]),
                "direction": "in",
                "type": m.get("message_type", "text"),
                "text": m.get("text", ""),
                "media_id": m.get("media_id"),
                "mime_type": m.get("mime_type"),
                "filename": m.get("filename"),
                "latitude": m.get("latitude"),
                "longitude": m.get("longitude"),
                "at": _iso(m.get("created_at")),
                "status": None,
                "automated": False,
                "sender": m.get("profile_name") or "Customer",
            })
        for m in outbound:
            kind = m.get("kind")
            if kind in ("otp", "temp_password"):
                text, mtype = "🔒 Security message (code hidden)", "redacted"
            else:
                text, mtype = m.get("message", ""), "media" if m.get("media_type") else ("template" if m.get("template_name") else "text")
            status = "FAILED" if m.get("ok") is False or m.get("delivery_status") == "failed" else {
                "read": "READ", "delivered": "DELIVERED"}.get(m.get("delivery_status"), "SENT")
            messages.append({
                "id": str(m["_id"]),
                "direction": "out",
                "type": mtype,
                "media_type": m.get("media_type"),
                "media_id": m.get("media_id"),
                "filename": m.get("filename"),
                "template_name": m.get("template_name"),
                "text": text,
                "at": _iso(m.get("created_at")),
                "status": status,
                "errors": m.get("delivery_errors"),
                "automated": kind != "agent",
                "sender": agents.get(m.get("agent_id", ""), "BLUSSIT") if kind == "agent" else "BLUSSIT",
            })
        messages.sort(key=lambda x: x["at"] or "")
        return {"conversation": await self._convo_out(convo), "messages": messages[-limit:]}

    async def mark_read(self, wa_id: str) -> None:
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"unread_count": 0}})

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def _require_convo(self, wa_id: str) -> dict:
        convo = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id})
        if not convo:
            raise NotFoundException("Conversation not found")
        return convo

    async def _after_agent_send(self, wa_id: str, text: str) -> None:
        await self.db.whatsapp_conversations.update_one(
            {"wa_id": wa_id},
            {"$set": {
                "last_message_text": text[:200], "last_message_at": datetime.now(timezone.utc),
                "last_message_direction": "out",
                # A human joined the thread: the booking bot must stop
                # reacting to this customer's replies until resolved.
                "bot_paused": True,
            }},
        )

    async def send_text(self, wa_id: str, text: str, agent_id: str) -> dict:
        convo = await self._require_convo(wa_id)
        if not text.strip():
            raise BadRequestException("Message is empty")
        if not self._window_state(convo)["active"]:
            raise BadRequestException("The 24-hour customer service window has expired — send an approved template instead.")
        ok = await self.wa.send_agent_text(convo.get("phone") or _local_phone(wa_id), text.strip(), agent_id)
        if not ok:
            await self._notify_admins("WhatsApp message failed", f"Free-text send to {convo.get('phone')} failed — check the outbox.")
            raise BadRequestException("WhatsApp did not accept the message — try again or use a template.")
        await self._after_agent_send(wa_id, text)
        return {"sent": True}

    async def send_template_message(self, wa_id_or_phone: str, template_name: str, params: list[str], agent_id: str) -> dict:
        tpl = await self._approved_template(template_name)
        phone = _local_phone(wa_id_or_phone)
        ok = await self.wa.send_agent_template(phone, template_name, tpl.get("language", "en_US"), [str(p) for p in params], agent_id)
        if not ok:
            raise BadRequestException("WhatsApp rejected the template send — check the parameters.")
        wa_id = phone if len(phone) != 10 else f"91{phone}"
        now = datetime.now(timezone.utc)
        await self.db.whatsapp_conversations.update_one(
            {"wa_id": wa_id},
            {"$set": {"phone": phone, "last_message_text": f"[template] {template_name}", "last_message_at": now, "last_message_direction": "out", "bot_paused": True},
             "$setOnInsert": {"wa_id": wa_id, "created_at": now, "tags": [], "unread_count": 0, "crm_status": "open"}},
            upsert=True,
        )
        return {"sent": True, "wa_id": wa_id}

    async def send_media_message(self, wa_id: str, media_type: str, media_id: str, caption: str, filename: str, agent_id: str) -> dict:
        convo = await self._require_convo(wa_id)
        if media_type not in ("image", "document", "video", "audio"):
            raise BadRequestException("Unsupported media type")
        if not self._window_state(convo)["active"]:
            raise BadRequestException("The 24-hour customer service window has expired — media needs an active window.")
        ok = await self.wa.send_agent_media(convo.get("phone") or _local_phone(wa_id), media_type, media_id, caption, filename, agent_id)
        if not ok:
            raise BadRequestException("WhatsApp did not accept the media message.")
        await self._after_agent_send(wa_id, caption or f"[{media_type}]")
        return {"sent": True}

    async def _approved_template(self, name: str) -> dict:
        tpl = await self.db.whatsapp_templates.find_one({"name": name})
        if not tpl:
            await self.sync_templates()
            tpl = await self.db.whatsapp_templates.find_one({"name": name})
        if not tpl or tpl.get("status") != "APPROVED" or tpl.get("disabled"):
            raise BadRequestException(f"Template '{name}' is not approved/enabled")
        return tpl

    # ------------------------------------------------------------------
    # Assignment / status / tags — every change audited by the routes.
    # ------------------------------------------------------------------

    async def assign(self, wa_id: str, user_id: str | None) -> dict:
        await self._require_convo(wa_id)
        name = None
        if user_id:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)}) if ObjectId.is_valid(user_id) else None
            if not user or user.get("role") not in ("admin", "manager"):
                raise BadRequestException("Assignee must be an admin or manager")
            name = user.get("full_name")
        await self.db.whatsapp_conversations.update_one(
            {"wa_id": wa_id}, {"$set": {"assigned_to": user_id, "assigned_to_name": name}})
        return {"assigned_to": user_id, "assigned_to_name": name}

    async def set_status(self, wa_id: str, status: str) -> dict:
        if status not in CRM_STATUSES:
            raise BadRequestException("Status must be open, pending or resolved")
        await self._require_convo(wa_id)
        update: dict = {"crm_status": status}
        if status == "resolved":
            update["resolved_at"] = datetime.now(timezone.utc)
            update["bot_paused"] = False  # resolve hands the customer back to the bot
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": update})
        return {"crm_status": status}

    async def set_tags(self, wa_id: str, tags: list[str]) -> dict:
        await self._require_convo(wa_id)
        clean = [t.strip()[:40] for t in tags if t.strip()][:12]
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"tags": clean}})
        return {"tags": clean}

    async def set_bot_paused(self, wa_id: str, paused: bool) -> dict:
        await self._require_convo(wa_id)
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"bot_paused": paused}})
        return {"bot_paused": paused}

    # ------------------------------------------------------------------
    # CRM context panel
    # ------------------------------------------------------------------

    async def contact_profile(self, wa_id: str) -> dict:
        convo = await self._require_convo(wa_id)
        out: dict = {"conversation": await self._convo_out(convo), "customer": None, "vehicles": [], "current_booking": None, "stats": None}
        customer_id = convo.get("customer_id")
        if not customer_id:
            # The bot links accounts lazily; try by phone.
            user = await self.db.users.find_one({"phone": convo.get("phone") or _local_phone(wa_id)})
            if user:
                customer_id = str(user["_id"])
                await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"customer_id": customer_id}})
        if not customer_id or not ObjectId.is_valid(customer_id):
            return out
        user = await self.db.users.find_one({"_id": ObjectId(customer_id)})
        if not user:
            return out
        out["customer"] = {
            "id": customer_id,
            "name": user.get("full_name"),
            "phone": user.get("phone"),
            "email": user.get("email"),
            "status": user.get("status"),
            "created_at": _iso(user.get("created_at")),
            "phone_verified": user.get("phone_verified", False),
        }
        vehicles = await self.db.vehicles.find({"owner_id": customer_id, "is_deleted": {"$ne": True}}).to_list(length=None)
        vt_names = {str(v["_id"]): v.get("name") for v in await self.db.vehicle_types.find({}).to_list(length=None)}
        out["vehicles"] = [
            {"brand": v.get("brand"), "model": v.get("model"), "registration_number": v.get("registration_number"),
             "type": vt_names.get(str(v.get("vehicle_type")), None)}
            for v in vehicles
        ]
        bookings = await self.db.bookings.find({"customer_id": customer_id, "is_deleted": {"$ne": True}}).sort("created_at", -1).to_list(length=None)
        completed = [b for b in bookings if b.get("status") == "completed"]
        cancelled = [b for b in bookings if b.get("status") == "cancelled"]
        active = next((b for b in bookings if b.get("status") in ("pending", "assigned", "on_the_way", "in_progress", "rescheduled")), None)
        if active:
            captain = None
            if active.get("captain_id") and ObjectId.is_valid(active["captain_id"]):
                cap = await self.db.users.find_one({"_id": ObjectId(active["captain_id"])})
                captain = cap.get("full_name") if cap else None
            svc_names = {str(x["_id"]): x.get("name") for x in await self.db.services.find({}).to_list(length=None)}
            out["current_booking"] = {
                "booking_number": active.get("booking_number"),
                "id": str(active["_id"]),
                "services": [svc_names.get(sid, "?") for sid in active.get("service_ids") or []],
                "date": str(active.get("scheduled_date"))[:10],
                "slot": active.get("scheduled_slot"),
                "status": active.get("status"),
                "captain": captain,
                "amount": active.get("total_amount"),
            }
        ratings = [r.get("captain_rating") or r.get("rating") for r in await self.db.reviews.find({"customer_id": customer_id, "is_deleted": {"$ne": True}}).to_list(length=None)]
        ratings = [r for r in ratings if r]
        last_completed = completed[0] if completed else None
        out["stats"] = {
            "total_bookings": len(bookings),
            "completed": len(completed),
            "cancelled": len(cancelled),
            "last_service": str(last_completed.get("scheduled_date"))[:10] if last_completed else None,
            "lifetime_value": round(sum(b.get("total_amount", 0) for b in completed), 2),
            "avg_rating_given": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "is_repeat": len(bookings) > 1,
        }
        return out

    async def contacts(self, search: str = "") -> list[dict]:
        rows = await self.list_conversations("all", search)
        return rows

    async def assignable_agents(self) -> list[dict]:
        agents = await self.db.users.find({"role": {"$in": ["admin", "manager"]}, "is_deleted": {"$ne": True}}).to_list(length=None)
        return [{"id": str(a["_id"]), "name": a.get("full_name"), "role": a.get("role")} for a in agents]

    async def unread_badge(self) -> dict:
        n = await self.db.whatsapp_conversations.count_documents({"unread_count": {"$gt": 0}})
        return {"unread_conversations": n}

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    async def sync_templates(self) -> dict:
        remote = await self.wa.list_templates()
        if remote is None:
            return {"synced": 0, "note": "Provider does not support template listing (log provider or missing WABA id)."}
        for t in remote:
            body = next((c.get("text", "") for c in t.get("components", []) if c.get("type") == "BODY"), "")
            await self.db.whatsapp_templates.update_one(
                {"name": t["name"]},
                {"$set": {
                    "name": t["name"], "status": t.get("status"), "category": t.get("category"),
                    "language": t.get("language"), "body": body,
                    "rejected_reason": None if t.get("rejected_reason") in (None, "NONE") else t.get("rejected_reason"),
                    "param_count": body.count("{{"),
                    "synced_at": datetime.now(timezone.utc),
                }, "$setOnInsert": {"disabled": False, "created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        return {"synced": len(remote)}

    async def list_local_templates(self) -> list[dict]:
        rows = await self.db.whatsapp_templates.find({}).sort("name", 1).to_list(length=None)
        usage = {u["_id"]: u["n"] for u in await self.db.whatsapp_outbox.aggregate([
            {"$match": {"template_name": {"$ne": None}}},
            {"$group": {"_id": "$template_name", "n": {"$sum": 1}}},
        ]).to_list(length=None)}
        return [{
            "name": r["name"], "status": r.get("status", "DRAFT"), "category": r.get("category"),
            "language": r.get("language"), "body": r.get("body", ""), "param_count": r.get("param_count", 0),
            "rejected_reason": r.get("rejected_reason"), "disabled": r.get("disabled", False),
            "usage": usage.get(r["name"], 0), "synced_at": _iso(r.get("synced_at")),
        } for r in rows]

    async def create_template(self, name: str, category: str, language: str, body: str, button_text: str | None, button_url: str | None) -> dict:
        if category not in ("UTILITY", "MARKETING", "AUTHENTICATION"):
            raise BadRequestException("Category must be UTILITY, MARKETING or AUTHENTICATION")
        components: list[dict] = [{"type": "BODY", "text": body}]
        n = body.count("{{")
        if n:
            components[0]["example"] = {"body_text": [[f"Sample {i + 1}" for i in range(n)]]}
        if button_text and button_url:
            components.append({"type": "BUTTONS", "buttons": [{"type": "URL", "text": button_text[:25], "url": button_url}]})
        result = await self.wa.create_template({"name": name, "language": language or "en_US", "category": category, "components": components})
        if result.get("error"):
            raise BadRequestException(f"Meta rejected the template: {result['error']}")
        await self.db.whatsapp_templates.update_one(
            {"name": name},
            {"$set": {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", category),
                      "language": language or "en_US", "body": body, "param_count": n, "disabled": False,
                      "synced_at": datetime.now(timezone.utc)},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", category)}

    async def set_template_disabled(self, name: str, disabled: bool) -> dict:
        r = await self.db.whatsapp_templates.update_one({"name": name}, {"$set": {"disabled": disabled}})
        if not r.matched_count:
            raise NotFoundException("Template not found")
        return {"name": name, "disabled": disabled}

    # ------------------------------------------------------------------
    # Automation: booking events -> dedicated templates when approved.
    # ------------------------------------------------------------------

    async def event_template_if_ready(self, event: str) -> dict | None:
        name = EVENT_TEMPLATES.get(event)
        if not name:
            return None
        tpl = await self.db.whatsapp_templates.find_one({"name": name})
        if tpl and tpl.get("status") == "APPROVED" and not tpl.get("disabled"):
            return tpl
        return None

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def analytics(self, days: int = 30) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        convs = await self.db.whatsapp_conversations.find({"last_message_at": {"$ne": None}}).to_list(length=None)
        total = len(convs)
        open_n = sum(1 for c in convs if c.get("crm_status", "open") in ("open", None))
        pending_n = sum(1 for c in convs if c.get("crm_status") == "pending")
        resolved_n = sum(1 for c in convs if c.get("crm_status") == "resolved")
        unread_n = sum(1 for c in convs if c.get("unread_count", 0) > 0)

        sent = await self.db.whatsapp_outbox.count_documents({"created_at": {"$gte": since}})
        received = await self.db.whatsapp_inbox.count_documents({"created_at": {"$gte": since}})
        tpl_rows = await self.db.whatsapp_outbox.find(
            {"created_at": {"$gte": since}, "template_name": {"$ne": None}},
            {"delivery_status": 1, "ok": 1},
        ).to_list(length=None)
        tpl_sent = len(tpl_rows)
        tpl_delivered = sum(1 for r in tpl_rows if r.get("delivery_status") in ("delivered", "read"))
        tpl_read = sum(1 for r in tpl_rows if r.get("delivery_status") == "read")
        tpl_failed = sum(1 for r in tpl_rows if r.get("ok") is False or r.get("delivery_status") == "failed")

        # First response: first outbound after the conversation's first
        # inbound. Resolution: created -> resolved_at.
        first_resp: list[float] = []
        resolution: list[float] = []
        for c in convs[:300]:
            first_in = await self.db.whatsapp_inbox.find_one({"wa_id": c["wa_id"]}, sort=[("created_at", 1)])
            if first_in:
                first_out = await self.db.whatsapp_outbox.find_one(
                    {"phone": {"$in": _wa_id_variants(c["wa_id"])}, "created_at": {"$gte": first_in["created_at"]}}, sort=[("created_at", 1)])
                if first_out:
                    first_resp.append((_aware(first_out["created_at"]) - _aware(first_in["created_at"])).total_seconds() / 60)
            if c.get("resolved_at") and c.get("created_at"):
                resolution.append((_aware(c["resolved_at"]) - _aware(c["created_at"])).total_seconds() / 3600)

        def _pct(a, b):
            return round(a / b * 100, 1) if b else None

        return {
            "days": days,
            "conversations": {"total": total, "open": open_n, "pending": pending_n, "resolved": resolved_n, "unread": unread_n},
            "messages": {"sent": sent, "received": received},
            "templates": {
                "sent": tpl_sent,
                "delivery_rate_pct": _pct(tpl_delivered, tpl_sent),
                "read_rate_pct": _pct(tpl_read, tpl_sent),
                "failure_rate_pct": _pct(tpl_failed, tpl_sent),
            },
            "avg_first_response_minutes": round(sum(first_resp) / len(first_resp), 1) if first_resp else None,
            "avg_resolution_hours": round(sum(resolution) / len(resolution), 1) if resolution else None,
        }


# ----------------------------------------------------------------------
# The 9 BLUSSIT operational templates + 1 future marketing draft.
# Submitted through the normal create_template path; marketing stays a
# LOCAL DRAFT only (never auto-submitted — consent rules differ).
# ----------------------------------------------------------------------
BLUSSIT_TEMPLATE_DEFS = [
    # NOTE: "welcome"-style copy ("Welcome to X!", promo lines, Book Now
    # buttons, emojis) is auto-filed as MARKETING by Meta's classifier —
    # blussit_welcome went live that way and stays on the WABA unused.
    # The utility-safe framing is an account-created confirmation.
    ("blussit_account_created", "UTILITY", "Hi {{1}}, your Blussit account has been created. Reply here anytime for help with your bookings.", None),
    ("blussit_booking_confirmed", "UTILITY", "Hi {{1}} 👋\n\nYour BLUSSIT booking is confirmed.\n\nService: {{2}}\nDate: {{3}}\nTime: {{4}}\nBooking ID: {{5}}\n\nWe'll see you at your doorstep!", "View Booking"),
    ("blussit_captain_assigned", "UTILITY", "Hi {{1}},\n\nYour BLUSSIT captain {{2}} has been assigned to your booking.\n\nService: {{3}}\nTime: {{4}}\n\nSee you soon!", "View Booking"),
    ("blussit_captain_on_the_way", "UTILITY", "Hi {{1}} 👋\n\nYour BLUSSIT captain is on the way.\n\nYour service will begin shortly.", "Track Booking"),
    ("blussit_service_completed", "UTILITY", "Hi {{1}} 👋\n\nYour BLUSSIT service has been completed.\n\nThank you for choosing BLUSSIT!", "Rate Service"),
    ("blussit_review_request", "UTILITY", "Hi {{1}} 👋\n\nHow was your BLUSSIT experience?\n\nYour feedback helps us improve.", "Leave a Review"),
    ("blussit_booking_reminder", "UTILITY", "Hi {{1}} 👋\n\nJust a reminder about your BLUSSIT booking.\n\nService: {{2}}\nDate: {{3}}\nTime: {{4}}\n\nSee you soon!", "View Booking"),
    ("blussit_reschedule_confirmation", "UTILITY", "Hi {{1}} 👋\n\nYour BLUSSIT booking has been rescheduled.\n\nNew date: {{2}}\nNew time: {{3}}\nBooking ID: {{4}}\n\nSee you at the new time!", "View Booking"),
    ("blussit_payment_confirmation", "UTILITY", "Hi {{1}} 👋\n\nYour payment of ₹{{2}} for booking {{3}} has been received.\n\nThank you for choosing BLUSSIT!", "View Booking"),
]

MARKETING_DRAFTS = [
    ("blussit_repeat_booking", "MARKETING", "Hi {{1}} 👋\n\nReady for your next BLUSSIT car wash?\n\nBook a doorstep wash whenever your car needs it.", "Book Now"),
]


async def bootstrap_blussit_templates(db: AsyncIOMotorDatabase) -> list[dict]:
    """Submits the operational templates that don't exist yet; records the
    marketing draft locally WITHOUT submitting it."""
    crm = WhatsAppCrmService(db)
    await crm.sync_templates()
    results = []
    for name, category, body, button in BLUSSIT_TEMPLATE_DEFS:
        existing = await db.whatsapp_templates.find_one({"name": name})
        if existing and existing.get("status") in ("APPROVED", "PENDING"):
            results.append({"name": name, "status": existing["status"], "note": "already exists"})
            continue
        try:
            r = await crm.create_template(name, category, "en_US", body, button, "https://blussit.com/")
            results.append(r)
        except BadRequestException as exc:
            results.append({"name": name, "status": "ERROR", "note": exc.message})
    for name, category, body, button in MARKETING_DRAFTS:
        await db.whatsapp_templates.update_one(
            {"name": name},
            {"$set": {"name": name, "status": "DRAFT", "category": category, "language": "en_US", "body": body,
                      "param_count": body.count("{{"), "disabled": False, "draft_button": button},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        results.append({"name": name, "status": "DRAFT", "note": "marketing draft — submit manually when consent flow is ready"})
    return results
