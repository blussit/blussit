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
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.services.whatsapp_service import (
    MetaCloudWhatsAppProvider,
    WhatsAppService,
    mask_phone,
    record_complete_template_sync,
)

logger = logging.getLogger(__name__)

CRM_STATUSES = ("open", "pending", "resolved")
DEFAULT_TAGS = [
    "New Customer", "Repeat Customer", "VIP", "Booking Issue", "Complaint",
    "Payment Issue", "Reschedule", "Feedback", "Follow-up Required",
]

# Booking events -> the dedicated template that should carry them once
# approved (see bootstrap_blussit_templates). Fallback is always the
# generic update template via send_generic. The booking-referencing
# events point at the short "_v4"/"_v5" templates (BLUSSIT_BOOKING_LINK_TEMPLATE_DEFS;
# _v5 = the _v4 text minus the "Code: {{n}}" line, which Meta rejected as
# INCORRECT_CATEGORY — a code in a Utility body reads like an OTP). Headers
# must be a DEFINITE status about the booking ("Captain assigned", "Your
# captain is on the way"): Meta re-labelled the "-ing" headers ("Captain
# heading out", "Assigning new captain") as MARKETING, which bills higher.
# whose button actually deep-links to the booking, not the old static
# ones — event_template_if_ready only returns a template once ITS name is
# Meta-approved, so these fall back to the generic template until then.
EVENT_TEMPLATES = {
    "welcome": "blussit_account_created",
    "booking_confirmed": "blussit_booking_confirmed_v5",
    "captain_assigned": "blussit_captain_assigned_v5",
    "captain_on_the_way": "blussit_captain_on_the_way_v6",
    # No call site yet (NTF-03): the arrival step should send it with
    # wa_params=[services, reference, date, slot, vehicle].
    "captain_arrived": "blussit_captain_arrived_v1",
    "service_started": "blussit_service_started_v5",
    "service_completed": "blussit_service_completed_v5",
    "review_request": "blussit_review_request",
    "booking_reminder": "blussit_booking_reminder_v5",
    "reschedule_confirmation": "blussit_reschedule_confirmation_v5",
    "payment_confirmation": "blussit_payment_confirmation_v4",
    "booking_cancelled": "blussit_booking_cancelled_v4",
    "payment_pending": "blussit_payment_pending_v4",
    "captain_released": "blussit_captain_released_v5",
    # Staff alert (to the center's managers) — v2 has an "Open Bookings"
    # button; v1 (no button) keeps working until v2 is approved.
    "manager_new_booking": "blussit_manager_new_booking_v2",
    "manager_new_booking_v2": "blussit_manager_new_booking_v2",
    # 2026-10-07 events (MONEY / BOOKING / PLANS call these — params in
    # CURRENT_TEMPLATE_DEFS and docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md):
    # booking_edited: [services, ref, date, slot, vehicle, new total (no ₹)]
    "booking_edited": "blussit_booking_edited_v1",
    # wallet_credited / wallet_debited: [name, amount (no ₹), reason, balance text (with ₹, may be negative)]
    "wallet_credited": "blussit_wallet_credited_v1",
    "wallet_debited": "blussit_wallet_debited_v1",
    # payment_failed: [services, ref, amount (no ₹)] — reference_id = booking id (retry button)
    "payment_failed": "blussit_payment_failed_v1",
    # booking_cancelled_v5: [services, ref, date, slot, "who cancelled + wallet line"]
    "booking_cancelled_v5": "blussit_booking_cancelled_wallet_v1",
    # review_request_google: [name, services] — NotificationService.send_review_requests only
    "review_request_google": "blussit_review_request_google_v1",
    # universal_message: [name, message] — NotificationService.send_universal_message
    "universal_message": "blussit_universal_message_v1",
    # book_now: [name] — MARKETING (send with wa_marketing=True)
    "book_now": "blussit_book_now_v1",
    # plan_expiring_v2: [name, plan, end date, washes left]
    "plan_expiring_v2": "blussit_plan_expiring_v2",
    # Manager-sold plan (see BLUSSIT_SUBSCRIPTION_LINK_TEMPLATE_DEFS).
    "subscription_payment_link": "blussit_subscription_payment_link_v1",
    "subscription_autopay_link": "blussit_subscription_autopay_link_v1",
    "subscription_activated": "blussit_subscription_activated",
    "subscription_renewed": "blussit_subscription_renewed",
    # Same params as plan_expiring_v2; the old template is its fallback.
    "subscription_expiring": "blussit_plan_expiring_v2",
    "subscription_expired": "blussit_subscription_expired",
    # "N washes left — book now" (main.remind_pass_washes): same approved
    # body ("ends on {{3}} with {{4}} washes left. Book them..."), its own
    # event name so it can move to a dedicated template later. Meta files
    # this template as MARKETING, so the sweep sends it wa_marketing.
    "pass_wash_reminder": "blussit_subscription_expiring",
    # Marketing — sent only through the approved template, only to
    # customers who haven't opted out (see NotificationService.notify).
    "repeat_booking": "blussit_repeat_booking",
    "we_miss_you": "blussit_we_miss_you",
}

# Older templates an event may still use while its preferred one is in
# review — ONLY when they take exactly the same parameters in the same
# order (booking_cancelled_v5's wallet line must never land in v4's
# "vehicle" slot, so it has none).
EVENT_TEMPLATE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "manager_new_booking": ("blussit_manager_new_booking_v1",),
    "manager_new_booking_v2": ("blussit_manager_new_booking_v1",),
    "subscription_expiring": ("blussit_subscription_expiring",),
    "plan_expiring_v2": ("blussit_subscription_expiring",),
}

WINDOW_HOURS = 24

# Who paused the bot on a thread (bot_paused_by): the bot's own handoff
# after two inputs it couldn't understand, or an agent (a manual message,
# an agent template, "Pause booking bot" in the inbox). Only a BOT handoff
# that no agent picked up expires — after BOT_HANDOFF_RESUME_HOURS the bot
# answers again, instead of staying silent for that customer forever. An
# agent's pause lasts until the thread is resolved or handed back. Older
# rows without bot_paused_by are treated as an agent's (never auto-resume).
PAUSED_BY_BOT = "bot"
PAUSED_BY_AGENT = "agent"
BOT_HANDOFF_RESUME_HOURS = 12

# Both spellings: the CRM originally listed "on_the_way"/"in_progress", but
# bookings actually move through captain_on_the_way/service_started — an
# in-flight booking never showed as the contact's current booking.
_ACTIVE_BOOKING_STATUSES = [
    "pending", "assigned", "captain_on_the_way", "service_started", "rescheduled", "on_the_way", "in_progress",
]
CONVERSATION_PAGE_MAX = 100


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime | None) -> str | None:
    a = _aware(dt)
    return a.isoformat() if a else None


def validate_https_url(raw) -> str:
    """"" (cleared) or an absolute https:// URL with a host and no spaces."""
    from urllib.parse import urlparse

    url = str(raw or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname
        or any(ch.isspace() for ch in url) or len(url) > 500
    ):
        raise BadRequestException("Enter a full https:// link (e.g. your Google review link)")
    return url


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
            logger.exception("Failed recording inbound WhatsApp message from %s", mask_phone(wa_id))

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
        convo = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id}, {"bot_paused": 1, "bot_paused_by": 1, "bot_paused_at": 1})
        if not (convo and convo.get("bot_paused")):
            return False
        paused_at = _aware(convo.get("bot_paused_at"))
        if (
            convo.get("bot_paused_by") == PAUSED_BY_BOT
            and paused_at
            and datetime.now(timezone.utc) - paused_at >= timedelta(hours=BOT_HANDOFF_RESUME_HOURS)
        ):
            # Guarded on the same handoff: an agent who took the thread
            # over meanwhile (bot_paused_by -> agent) keeps it.
            await self.db.whatsapp_conversations.update_one(
                {"wa_id": wa_id, "bot_paused": True, "bot_paused_by": PAUSED_BY_BOT, "bot_paused_at": convo["bot_paused_at"]},
                {"$set": {"bot_paused": False}, "$unset": {"bot_paused_by": "", "bot_paused_at": ""}},
            )
            fresh = await self.db.whatsapp_conversations.find_one({"wa_id": wa_id}, {"bot_paused": 1})
            return bool(fresh and fresh.get("bot_paused"))
        return True

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

    async def list_conversations(
        self, filter_key: str = "all", search: str = "", agent_id: str | None = None,
        limit: int = 50, before: str | None = None,
    ) -> list[dict]:
        """Newest-first page of conversations. `before` is the
        last_message_at (ISO) of the last row the caller already has — the
        next page starts strictly older than it."""
        limit = max(1, min(limit, CONVERSATION_PAGE_MAX))
        query: dict = {"last_message_at": {"$ne": None}}  # bot-state-only docs have no traffic
        if before:
            try:
                cursor_at = datetime.fromisoformat(before.replace("Z", "+00:00"))
            except ValueError:
                raise BadRequestException("before must be an ISO timestamp")
            query["last_message_at"] = {"$ne": None, "$lt": cursor_at}
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

        # "booking"/"new_customer" are decided per row below, so a page can
        # need more than `limit` conversations scanned to fill it.
        post_filtered = filter_key in ("booking", "new_customer")
        batch = limit * 3 if post_filtered else limit
        out: list[dict] = []
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        for _ in range(5 if post_filtered else 1):
            rows = await self.db.whatsapp_conversations.find(query).sort("last_message_at", -1).limit(batch).to_list(length=batch)
            if not rows:
                break
            customer_ids = [ObjectId(r["customer_id"]) for r in rows if r.get("customer_id") and ObjectId.is_valid(r["customer_id"])]
            users = (
                {str(u["_id"]): u for u in await self.db.users.find({"_id": {"$in": customer_ids}}, {"full_name": 1, "created_at": 1}).to_list(length=None)}
                if customer_ids else {}
            )
            names = {uid: u.get("full_name", "") for uid, u in users.items()}
            # Booking indicator + the two customer-derived filters.
            active_by_customer: set[str] = set()
            cids = [r["customer_id"] for r in rows if r.get("customer_id")]
            if cids:
                async for b in self.db.bookings.aggregate([
                    {"$match": {"customer_id": {"$in": cids}, "status": {"$in": _ACTIVE_BOOKING_STATUSES}, "is_deleted": {"$ne": True}}},
                    {"$group": {"_id": "$customer_id"}},
                ]):
                    active_by_customer.add(b["_id"])
            for r in rows:
                r["_has_active_booking"] = r.get("customer_id") in active_by_customer
                if filter_key == "booking" and not r["_has_active_booking"]:
                    continue
                if filter_key == "new_customer":
                    u = users.get(r.get("customer_id") or "")
                    if not u or _aware(u.get("created_at", week_ago)) < week_ago:
                        continue
                out.append(await self._convo_out(r, names))
                if len(out) >= limit:
                    return out
            if len(rows) < batch:
                break
            query["last_message_at"] = {"$ne": None, "$lt": rows[-1]["last_message_at"]}
        return out

    async def _search_wa_ids(self, search: str) -> list[str] | None:
        """Resolves a free-text search (name / phone / booking no / vehicle
        reg) to the matching conversation wa_ids."""
        s = search.strip()
        wa_ids: set[str] = set()
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 10:
            variants = _wa_id_variants(digits)
            async for c in self.db.whatsapp_conversations.find({"$or": [{"wa_id": {"$in": variants}}, {"phone": {"$in": variants}}]}, {"wa_id": 1}):
                wa_ids.add(c["wa_id"])
        elif len(digits) >= 4:
            # Anchored prefixes stay index-served (wa_id unique index,
            # phone index); an unanchored regex scanned every conversation.
            prefix = {"$or": [{"phone": {"$regex": f"^{digits}"}}, {"wa_id": {"$regex": f"^(91)?{digits}"}}]}
            async for c in self.db.whatsapp_conversations.find(prefix, {"wa_id": 1}).limit(200):
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

        variants = sorted({v for phone in phones for v in _wa_id_variants(phone)})
        if variants:
            async for c in self.db.whatsapp_conversations.find({"$or": [{"wa_id": {"$in": variants}}, {"phone": {"$in": variants}}]}, {"wa_id": 1}):
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
        now = datetime.now(timezone.utc)
        await self.db.whatsapp_conversations.update_one(
            {"wa_id": wa_id},
            {"$set": {
                "last_message_text": text[:200], "last_message_at": now,
                "last_message_direction": "out",
                # A human joined the thread: the booking bot must stop
                # reacting to this customer's replies until resolved.
                "bot_paused": True, "bot_paused_by": PAUSED_BY_AGENT, "bot_paused_at": now,
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
            {"$set": {"phone": phone, "last_message_text": f"[template] {template_name}", "last_message_at": now, "last_message_direction": "out",
                      "bot_paused": True, "bot_paused_by": PAUSED_BY_AGENT, "bot_paused_at": now},
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
        update: dict = {"$set": {"crm_status": status}}
        if status == "resolved":
            update["$set"]["resolved_at"] = datetime.now(timezone.utc)
            update["$set"]["bot_paused"] = False  # resolve hands the customer back to the bot
            # ...and a later question is a new issue: the bot may ping the
            # admins for it again (see WhatsAppBotService._ping_admins).
            update["$unset"] = {"bot_paused_by": "", "bot_paused_at": "", "admin_pinged_at": ""}
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, update)
        return {"crm_status": status}

    async def set_tags(self, wa_id: str, tags: list[str]) -> dict:
        await self._require_convo(wa_id)
        clean = [t.strip()[:40] for t in tags if t.strip()][:12]
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"tags": clean}})
        return {"tags": clean}

    async def set_bot_paused(self, wa_id: str, paused: bool) -> dict:
        await self._require_convo(wa_id)
        if paused:
            update = {"$set": {"bot_paused": True, "bot_paused_by": PAUSED_BY_AGENT, "bot_paused_at": datetime.now(timezone.utc)}}
        else:
            update = {"$set": {"bot_paused": False}, "$unset": {"bot_paused_by": "", "bot_paused_at": ""}}
        await self.db.whatsapp_conversations.update_one({"wa_id": wa_id}, update)
        return {"bot_paused": paused}

    # ------------------------------------------------------------------
    # CRM context panel
    # ------------------------------------------------------------------

    async def contact_profile(self, wa_id: str) -> dict:
        convo = await self._require_convo(wa_id)
        out: dict = {"conversation": await self._convo_out(convo), "customer": None, "vehicles": [], "current_booking": None, "stats": None}
        customer_id = convo.get("customer_id")
        if not customer_id:
            # The bot links accounts lazily; try by phone. Only a CUSTOMER
            # account is linked — a staff phone's chat is shown, never tied
            # to the staff account as if it were a customer (AUTH-05).
            user = await self.db.users.find_one({"phone": convo.get("phone") or _local_phone(wa_id)})
            if user:
                customer_id = str(user["_id"])
                if user.get("role") == "customer":
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
        vehicles = await self.db.vehicles.find({"owner_id": customer_id, "is_deleted": {"$ne": True}}).to_list(length=50)
        vt_names = {str(v["_id"]): v.get("name") for v in await self.db.vehicle_types.find({}, {"name": 1}).to_list(length=None)}
        out["vehicles"] = [
            {"brand": v.get("brand"), "model": v.get("model"), "registration_number": v.get("registration_number"),
             "type": vt_names.get(str(v.get("vehicle_type")), None)}
            for v in vehicles
        ]
        live = {"customer_id": customer_id, "is_deleted": {"$ne": True}}
        totals_rows, last_completed, active, rating_rows = await asyncio.gather(
            self.db.bookings.aggregate([
                {"$match": live},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                    "cancelled": {"$sum": {"$cond": [{"$eq": ["$status", "cancelled"]}, 1, 0]}},
                    "lifetime": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, "$total_amount", 0]}},
                }},
            ]).to_list(length=1),
            self.db.bookings.find_one({**live, "status": "completed"}, {"scheduled_date": 1}, sort=[("created_at", -1)]),
            self.db.bookings.find_one({**live, "status": {"$in": _ACTIVE_BOOKING_STATUSES}}, sort=[("created_at", -1)]),
            self.db.reviews.aggregate([
                {"$match": {"customer_id": customer_id, "is_deleted": {"$ne": True}}},
                {"$project": {"r": {"$cond": ["$captain_rating", "$captain_rating", "$rating"]}}},
                {"$match": {"r": {"$nin": [None, 0]}}},
                {"$group": {"_id": None, "avg": {"$avg": "$r"}}},
            ]).to_list(length=1),
        )
        if active:
            captain = None
            if active.get("captain_id") and ObjectId.is_valid(active["captain_id"]):
                cap = await self.db.users.find_one({"_id": ObjectId(active["captain_id"])}, {"full_name": 1})
                captain = cap.get("full_name") if cap else None
            sids = [ObjectId(x) for x in active.get("service_ids") or [] if ObjectId.is_valid(x)]
            svc_names = {str(x["_id"]): x.get("name") for x in await self.db.services.find({"_id": {"$in": sids}}, {"name": 1}).to_list(length=None)} if sids else {}
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
        totals = totals_rows[0] if totals_rows else {}
        avg_rating = rating_rows[0]["avg"] if rating_rows else None
        out["stats"] = {
            "total_bookings": totals.get("total", 0),
            "completed": totals.get("completed", 0),
            "cancelled": totals.get("cancelled", 0),
            "last_service": str(last_completed.get("scheduled_date"))[:10] if last_completed else None,
            "lifetime_value": round(totals.get("lifetime") or 0, 2),
            "avg_rating_given": round(avg_rating, 1) if avg_rating else None,
            "is_repeat": totals.get("total", 0) > 1,
        }
        return out

    async def contacts(self, search: str = "", limit: int = 50, before: str | None = None) -> list[dict]:
        return await self.list_conversations("all", search, limit=limit, before=before)

    async def assignable_agents(self) -> list[dict]:
        agents = await self.db.users.find(
            {"role": {"$in": ["admin", "manager"]}, "is_deleted": {"$ne": True}}, {"full_name": 1, "role": 1}
        ).sort("full_name", 1).to_list(length=500)
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
            buttons_component = next((c for c in t.get("components", []) if c.get("type") == "BUTTONS"), None)
            url_button = next(
                (b for b in (buttons_component or {}).get("buttons", []) if b.get("type") == "URL"), None
            )
            # Authoritative, straight from Meta — whatever the template
            # ACTUALLY has approved, not just what we asked for at
            # creation time (in case it was edited directly on Meta's
            # side). Only a URL button with a "{{" in it takes a
            # per-send parameter; a static one must never be sent one.
            has_url_param = bool(
                buttons_component
                and any(b.get("type") == "URL" and "{{" in (b.get("url") or "") for b in buttons_component.get("buttons", []))
            )
            await self.db.whatsapp_templates.update_one(
                {"name": t["name"]},
                {"$set": {
                    "name": t["name"], "status": t.get("status"), "category": t.get("category"),
                    "language": t.get("language"), "body": body,
                    "rejected_reason": None if t.get("rejected_reason") in (None, "NONE") else t.get("rejected_reason"),
                    "param_count": body.count("{{"),
                    "has_url_param": has_url_param,
                    "button_url": (url_button or {}).get("url"),
                    "synced_at": datetime.now(timezone.utc),
                }, "$setOnInsert": {"disabled": False, "created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        # Meta's WHOLE list arrived (list_templates returns None for a
        # partial one): from now on a configured name that isn't in it is
        # unusable (WhatsAppService._usable_template).
        await record_complete_template_sync(self.db, len(remote))
        return {"synced": len(remote)}

    async def sync_templates_if_due(self, max_age_minutes: int = 60) -> dict:
        """sync_templates at most once per `max_age_minutes` across every
        instance (claimed on a db.locks doc) — for boot and the hourly loop
        (DEP-04): an approval, pause or rejection on Meta's side reaches
        event_template_if_ready / the update template without anyone
        pressing Sync. A failed sync is retried after ~5 minutes."""
        now = datetime.now(timezone.utc)
        lock_id = "whatsapp_template_sync"
        try:
            await self.db.locks.find_one_and_update(
                {"_id": lock_id, "$or": [{"synced_at": {"$lt": now - timedelta(minutes=max_age_minutes)}}, {"synced_at": {"$exists": False}}]},
                {"$set": {"synced_at": now}},
                upsert=True,
            )
        except DuplicateKeyError:
            return {"skipped": True, "reason": "synced recently"}
        try:
            result = await self.sync_templates()
        except Exception:  # noqa: BLE001 — retried by the next due call
            logger.exception("WhatsApp template sync failed")
            result = {"synced": 0, "error": "sync failed"}
        failed = "error" in result or (
            "note" in result and isinstance(self.wa.provider, MetaCloudWhatsAppProvider) and settings.WHATSAPP_BUSINESS_ACCOUNT_ID
        )
        if failed:
            retry_at = now - timedelta(minutes=max(max_age_minutes - 5, 0))
            await self.db.locks.update_one({"_id": lock_id, "synced_at": now}, {"$set": {"synced_at": retry_at}})
        return result

    async def apply_template_event(self, field: str, value: dict) -> None:
        """Meta's template webhooks: `message_template_status_update`
        (APPROVED / REJECTED / PAUSED / DISABLED / …) and
        `template_category_update`. Updates the local row at once; a
        template we don't know yet is fetched by a full sync."""
        name = value.get("message_template_name")
        if not name:
            return
        now = datetime.now(timezone.utc)
        if field == "template_category_update":
            category = value.get("new_category")
            if category:
                await self.db.whatsapp_templates.update_one(
                    {"name": name},
                    {"$set": {"category": category, "previous_category": value.get("previous_category"), "category_updated_at": now}},
                )
                # Honoured on every send (NotificationService._deliver): a
                # MARKETING template no longer reaches opted-out customers.
                logger.warning(
                    "WhatsApp template %s re-categorised %s -> %s by Meta",
                    name, value.get("previous_category") or "?", category,
                )
            return
        event = str(value.get("event") or "").upper()
        if not event:
            return
        # REINSTATED = approved again; FLAGGED = quality warning, still sendable.
        status = "APPROVED" if event in ("REINSTATED", "FLAGGED") else event
        reason = value.get("reason")
        result = await self.db.whatsapp_templates.update_one(
            {"name": name},
            {"$set": {
                "status": status, "last_status_event": event, "status_updated_at": now,
                "rejected_reason": None if reason in (None, "NONE") else reason,
            }},
        )
        logger.info("WhatsApp template %s is now %s (%s)", name, status, reason or "-")
        if not result.matched_count and status == "APPROVED":
            await self.sync_templates()  # its body/param count are needed to send it

    async def list_local_templates(self) -> list[dict]:
        rows = await self.db.whatsapp_templates.find({}).sort("name", 1).to_list(length=None)
        # One COUNT_SCAN per template on (template_name, created_at) — the
        # old $group read every outbox row ever sent.
        counts = await asyncio.gather(*(self.db.whatsapp_outbox.count_documents({"template_name": r["name"]}) for r in rows))
        usage = {r["name"]: n for r, n in zip(rows, counts)}
        return [{
            "name": r["name"], "status": r.get("status", "DRAFT"), "category": r.get("category"),
            "language": r.get("language"), "body": r.get("body", ""), "param_count": r.get("param_count", 0),
            "rejected_reason": r.get("rejected_reason"), "disabled": r.get("disabled", False),
            "usage": usage.get(r["name"], 0), "synced_at": _iso(r.get("synced_at")),
        } for r in rows]

    async def agent_sendable_templates(self) -> list[dict]:
        """What an agent can actually send from the inbox: approved, not
        switched off, no URL-button parameter and not an OTP template — the
        agent send never fills those, so Meta rejects every such attempt."""
        blocked = set(await self.db.whatsapp_templates.distinct("name", {"has_url_param": True}))
        return [
            t for t in await self.list_local_templates()
            if t["status"] == "APPROVED" and not t["disabled"] and t["name"] not in blocked and t.get("category") != "AUTHENTICATION"
        ]

    async def create_template(
        self, name: str, category: str, language: str, body: str, button_text: str | None, button_url: str | None,
        examples: list[str] | None = None,
    ) -> dict:
        """`examples`: one realistic value per {{n}} — Meta reviews the
        template WITH them, and "Sample 1" placeholders get rejected."""
        if category not in ("UTILITY", "MARKETING", "AUTHENTICATION"):
            raise BadRequestException("Category must be UTILITY, MARKETING or AUTHENTICATION")
        components: list[dict] = [{"type": "BODY", "text": body}]
        n = body.count("{{")
        if n:
            values = [str(e) for e in (examples or [])][:n]
            values += [f"Sample {i + 1}" for i in range(len(values), n)]
            components[0]["example"] = {"body_text": [values]}
        # A URL button with its own {{1}} (e.g. ".../app/bookings/{{1}}")
        # needs its own example too, or Meta rejects the submission — the
        # static ones (no "{{") don't take one at all.
        has_url_param = bool(button_url and "{{" in button_url)
        if button_text and button_url:
            button: dict = {"type": "URL", "text": button_text[:25], "url": button_url}
            if has_url_param:
                button["example"] = [button_url.replace("{{1}}", "000000000000000000000000")]
            components.append({"type": "BUTTONS", "buttons": [button]})
        result = await self.wa.create_template({"name": name, "language": language or "en_US", "category": category, "components": components})
        if result.get("error"):
            raise BadRequestException(f"Meta rejected the template: {result['error']}")
        await self.db.whatsapp_templates.update_one(
            {"name": name},
            {"$set": {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", category),
                      "language": language or "en_US", "body": body, "param_count": n, "disabled": False,
                      "has_url_param": has_url_param, "button_url": button_url if button_text else None,
                      "synced_at": datetime.now(timezone.utc)},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"name": name, "status": result.get("status", "PENDING"), "category": result.get("category", category)}

    async def create_otp_template(self, name: str, language: str = "en_US", code_expiration_minutes: int = 10) -> dict:
        """The real fix for "OTP never arrives for a first-time customer":
        send_otp's fallback (see WhatsAppService.send_otp) is a plain
        free-text message, which WhatsApp only ever delivers to someone
        who has an OPEN 24h SESSION with the business number — i.e. it
        works for a re-verification or for testing against your own
        number (which has messaged the bot before), but silently drops
        for a genuinely brand-new signup, who has never messaged us. An
        AUTHENTICATION-category template is the one message type Meta
        lets through to a cold contact for a one-time code, and it
        DEMANDS this exact shape — no custom body text at all: Meta
        generates "{{1}} is your verification code." itself, adds the
        security-recommendation and expiry lines, and the OTP arrives
        with a native "Copy Code" button the customer taps to copy it
        straight to their clipboard (no "type out 6 digits" needed) —
        this is also the closest thing to autofill a WEBSITE (not a
        native app, which is what Meta's other ONE_TAP button variant
        needs) can offer. Once Meta approves this, point
        WHATSAPP_OTP_TEMPLATE_NAME at `name` and restart — see
        WhatsAppService.send_otp, which already checks whether the
        configured template is this AUTHENTICATION kind."""
        payload = {
            "name": name,
            "language": language,
            "category": "AUTHENTICATION",
            "components": [
                {"type": "BODY", "add_security_recommendation": True},
                {"type": "FOOTER", "code_expiration_minutes": code_expiration_minutes},
                {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy Code"}]},
            ],
        }
        result = await self.wa.create_template(payload)
        if result.get("error"):
            raise BadRequestException(f"Meta rejected the OTP template: {result['error']}")
        await self.db.whatsapp_templates.update_one(
            {"name": name},
            {"$set": {"name": name, "status": result.get("status", "PENDING"), "category": "AUTHENTICATION",
                      "language": language, "body": "(Meta-generated OTP text — no custom body for this category)",
                      "param_count": 1, "disabled": False, "synced_at": datetime.now(timezone.utc)},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"name": name, "status": result.get("status", "PENDING"), "category": "AUTHENTICATION"}

    async def set_template_disabled(self, name: str, disabled: bool) -> dict:
        r = await self.db.whatsapp_templates.update_one({"name": name}, {"$set": {"disabled": disabled}})
        if not r.matched_count:
            raise NotFoundException("Template not found")
        return {"name": name, "disabled": disabled}

    # ------------------------------------------------------------------
    # Automation: booking events -> dedicated templates when approved.
    # ------------------------------------------------------------------

    async def event_template_if_ready(self, event: str) -> dict | None:
        """The approved template that carries `event` right now: the
        preferred one (or a newer version of it, submitted after a
        rejection — see submit_template_def), else a same-parameter
        fallback. None = nothing approved (notify uses the generic)."""
        name = EVENT_TEMPLATES.get(event)
        if not name:
            return None
        for row in await _family_rows(self.db, name):
            if row.get("status") == "APPROVED" and not row.get("disabled"):
                return row
        for fallback in EVENT_TEMPLATE_FALLBACKS.get(event, ()):
            tpl = await self.db.whatsapp_templates.find_one({"name": fallback})
            if tpl and tpl.get("status") == "APPROVED" and not tpl.get("disabled"):
                return tpl
        return None

    # ------------------------------------------------------------------
    # Settings (admin): the Google review URL
    # ------------------------------------------------------------------

    SETTINGS_KEY = "whatsapp_settings"

    async def get_settings(self) -> dict:
        doc = await self.db.settings.find_one({"key": self.SETTINGS_KEY}, {"value": 1})
        value = (doc or {}).get("value") or {}
        return {"google_review_url": value.get("google_review_url") or ""}

    async def update_settings(self, changes: dict, actor_id: str | None = None) -> dict:
        """Only known keys; the URL must be https:// (it becomes a WhatsApp
        button, opened by customers). "" clears it (review requests stop)."""
        from app.repositories.content_repository import SettingRepository

        current = await self.get_settings()
        if "google_review_url" in changes:
            current["google_review_url"] = validate_https_url(changes.get("google_review_url"))
        await SettingRepository(self.db).upsert(
            self.SETTINGS_KEY, current, "WhatsApp settings (Google review URL)", updated_by=actor_id
        )
        return current

    # ------------------------------------------------------------------
    # Template catalogue (Admin → WhatsApp → Templates → Submit)
    # ------------------------------------------------------------------

    async def template_catalogue(self) -> list[dict]:
        """Every current definition with what Meta has for it and what
        Submit would send now."""
        out = []
        review_url = (await self.get_settings()).get("google_review_url")
        for d in CURRENT_TEMPLATE_DEFS:
            plan = await _submission_plan(self.db, d)
            family = await _family_rows(self.db, d["name"])
            newest = family[0] if family else None
            out.append({
                "key": template_key(d["name"]),
                "name": d["name"],
                "live_name": newest["name"] if newest else None,
                "status": (newest or {}).get("status") or "NOT_SUBMITTED",
                "meta_category": (newest or {}).get("category"),
                "rejected_reason": (newest or {}).get("rejected_reason"),
                "can_submit": plan["action"] == "submit",
                "submit_name": plan["name"] if plan["action"] == "submit" else None,
                "category": d["category"],
                "body": d["body"],
                "examples": d["examples"],
                "button_text": d.get("button_text"),
                "button_url": review_url if d.get("button_url") == GOOGLE_REVIEW_URL_SETTING else d.get("button_url"),
                "events": _events_for(d["name"]),
                "note": d.get("note") or "",
            })
        return out

    async def submit_catalogue_template(self, key: str) -> dict:
        d = next((d for d in CURRENT_TEMPLATE_DEFS if template_key(d["name"]) == key), None)
        if not d:
            raise NotFoundException("No such template in the catalogue")
        result = await submit_template_def(self.db, d)
        if result.get("status") in ("ERROR", "SKIPPED"):
            raise BadRequestException(result.get("note") or "Could not submit the template")
        return result

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def analytics(self, days: int = 30) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with_traffic = {"last_message_at": {"$ne": None}}
        status = {"$ifNull": ["$crm_status", "open"]}
        template_rows = {"created_at": {"$gte": since}, "template_name": {"$ne": None}}
        conv_rows, sent, received, tpl_rows, sample = await asyncio.gather(
            self.db.whatsapp_conversations.aggregate([
                {"$match": with_traffic},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "open": {"$sum": {"$cond": [{"$eq": [status, "open"]}, 1, 0]}},
                    "pending": {"$sum": {"$cond": [{"$eq": ["$crm_status", "pending"]}, 1, 0]}},
                    "resolved": {"$sum": {"$cond": [{"$eq": ["$crm_status", "resolved"]}, 1, 0]}},
                    "unread": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$unread_count", 0]}, 0]}, 1, 0]}},
                }},
            ]).to_list(length=1),
            self.db.whatsapp_outbox.count_documents({"created_at": {"$gte": since}}),
            self.db.whatsapp_inbox.count_documents({"created_at": {"$gte": since}}),
            self.db.whatsapp_outbox.aggregate([
                {"$match": template_rows},
                {"$group": {
                    "_id": None,
                    "sent": {"$sum": 1},
                    "delivered": {"$sum": {"$cond": [{"$in": ["$delivery_status", ["delivered", "read"]]}, 1, 0]}},
                    "read": {"$sum": {"$cond": [{"$eq": ["$delivery_status", "read"]}, 1, 0]}},
                    "failed": {"$sum": {"$cond": [{"$or": [{"$eq": ["$ok", False]}, {"$eq": ["$delivery_status", "failed"]}]}, 1, 0]}},
                }},
            ]).to_list(length=1),
            # Response/resolution times are sampled over the 300 most
            # recently active conversations (previously an arbitrary 300).
            self.db.whatsapp_conversations.find(with_traffic, {"wa_id": 1, "created_at": 1, "resolved_at": 1})
            .sort("last_message_at", -1).limit(300).to_list(length=300),
        )
        c = conv_rows[0] if conv_rows else {}
        t = tpl_rows[0] if tpl_rows else {}
        tpl_sent = t.get("sent", 0)

        # First response: first outbound after the conversation's first
        # inbound. Resolution: created -> resolved_at.
        wa_ids = [conv["wa_id"] for conv in sample]
        first_in = {
            r["_id"]: r["first"]
            for r in await self.db.whatsapp_inbox.aggregate([
                {"$match": {"wa_id": {"$in": wa_ids}}},
                {"$group": {"_id": "$wa_id", "first": {"$min": "$created_at"}}},
            ]).to_list(length=None)
        } if wa_ids else {}
        limiter = asyncio.Semaphore(10)

        async def first_reply(wa_id: str, after: datetime):
            async with limiter:
                return await self.db.whatsapp_outbox.find_one(
                    {"phone": {"$in": _wa_id_variants(wa_id)}, "created_at": {"$gte": after}},
                    {"created_at": 1}, sort=[("created_at", 1)],
                )

        pending_ids = [w for w in wa_ids if first_in.get(w)]
        replies = await asyncio.gather(*(first_reply(w, first_in[w]) for w in pending_ids))
        first_resp = [
            (_aware(out["created_at"]) - _aware(first_in[w])).total_seconds() / 60
            for w, out in zip(pending_ids, replies) if out
        ]
        resolution = [
            (_aware(conv["resolved_at"]) - _aware(conv["created_at"])).total_seconds() / 3600
            for conv in sample if conv.get("resolved_at") and conv.get("created_at")
        ]

        def _pct(a, b):
            return round(a / b * 100, 1) if b else None

        return {
            "days": days,
            "conversations": {
                "total": c.get("total", 0), "open": c.get("open", 0), "pending": c.get("pending", 0),
                "resolved": c.get("resolved", 0), "unread": c.get("unread", 0),
            },
            "messages": {"sent": sent, "received": received},
            "templates": {
                "sent": tpl_sent,
                "delivery_rate_pct": _pct(t.get("delivered", 0), tpl_sent),
                "read_rate_pct": _pct(t.get("read", 0), tpl_sent),
                "failure_rate_pct": _pct(t.get("failed", 0), tpl_sent),
            },
            "avg_first_response_minutes": round(sum(first_resp) / len(first_resp), 1) if first_resp else None,
            "avg_resolution_hours": round(sum(resolution) / len(resolution), 1) if resolution else None,
        }


# ----------------------------------------------------------------------
# The template catalogue (2026-10-07).
#
# CURRENT_TEMPLATE_DEFS is the ONE list bootstrap / "Submit" sends to Meta:
# every template an EVENT_TEMPLATES entry (or the generic fallback) uses
# today. Templates replaced by a newer version are listed in
# SUPERSEDED_TEMPLATE_NAMES and never submitted again — they stay on the
# WABA (an approved template that's never sent costs nothing).
#
# Versioning: a name Meta REJECTED is never resubmitted unchanged (Meta
# refuses a duplicate name anyway). Submitting a definition whose latest
# version was rejected uses the NEXT version name (…_v1 rejected → …_v2),
# and event_template_if_ready picks up whichever version Meta approves.
# A definition whose copy (param count / meaning) changes must get a new
# version in code — the old approved one keeps working meanwhile.
#
# Founder's rules for the copy: short; headings and buttons in Title Case;
# car type + service on booking messages; never a code in a Utility body
# (Meta re-files it as an OTP-like / marketing message); no promo wording
# in Utility templates (Meta re-files them as MARKETING, which bills more
# and respects opt-outs — see NotificationService._deliver).
# ----------------------------------------------------------------------
SITE = "https://blussit.com"
BOOKING_LINK = f"{SITE}/app/bookings/{{{{1}}}}"  # "…/app/bookings/{{1}}", filled with the booking id

_BOOKING_EXAMPLE = ["Star Wash", "BK-1042", "12 Oct 2026", "9:00 AM – 12:00 PM", "Hatchback"]


def _def(name: str, category: str, body: str, examples: list[str], button_text: str | None = None,
         button_url: str | None = None, note: str = "") -> dict:
    return {"name": name, "category": category, "body": body, "examples": examples,
            "button_text": button_text, "button_url": button_url, "note": note}


# Marker for a button whose URL is an admin setting, resolved at submit time.
GOOGLE_REVIEW_URL_SETTING = "setting:google_review_url"

CURRENT_TEMPLATE_DEFS: list[dict] = [
    # -- account ---------------------------------------------------------
    _def("blussit_account_created", "UTILITY",
         "Hi {{1}}, your Blussit account has been created. Reply here anytime for help with your bookings.",
         ["Asha"]),
    # Staff password reset (WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME): one {{1}}
    # = the temporary password. Reaches staff outside the 24 h chat window.
    # Staff temp-password template: Meta REJECTED both wordings tried
    # (blussit_staff_password_v1/_v2 — it treats a password in a UTILITY
    # message as an authentication message). Not re-submitted; staff resets
    # use free text inside an open 24 h chat (see send_temp_password_outcome).
    # -- booking lifecycle (button deep-links to the booking) -------------
    _def("blussit_booking_confirmed_v5", "UTILITY",
         "✅ Booking confirmed\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK),
    _def("blussit_captain_assigned_v5", "UTILITY",
         "🧑‍🔧 Captain assigned\n👤 {{1}}\n🚗 {{2}} ({{3}})\n📅 {{4}} · {{5}}\n🚙 {{6}}\nCall us for any query.",
         ["Ravi", *_BOOKING_EXAMPLE], "View Booking", BOOKING_LINK),
    _def("blussit_captain_on_the_way_v6", "UTILITY",
         "🚗 Your captain is on the way\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "Track Booking", BOOKING_LINK),
    _def("blussit_captain_arrived_v1", "UTILITY",
         "📍 Your captain has arrived\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK),
    _def("blussit_service_started_v5", "UTILITY",
         "🧽 Service started\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "Track Booking", BOOKING_LINK),
    _def("blussit_service_completed_v5", "UTILITY",
         "✅ Service done\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "Rate Now", BOOKING_LINK),
    _def("blussit_booking_reminder_v5", "UTILITY",
         "⏰ Booking reminder\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK, "Pre-slot reminder (NotificationService.send_slot_reminders)"),
    _def("blussit_reschedule_confirmation_v5", "UTILITY",
         "🔁 Booking rescheduled\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK),
    _def("blussit_booking_edited_v1", "UTILITY",
         "✏️ Booking Updated\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\n💰 New Total: ₹{{6}}\nCall us for any query.",
         [*_BOOKING_EXAMPLE, "499"], "View Booking", BOOKING_LINK),
    _def("blussit_booking_cancelled_v4", "UTILITY",
         "❌ Booking cancelled\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK),
    _def("blussit_booking_cancelled_wallet_v1", "UTILITY",
         "❌ Booking Cancelled\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n💳 {{5}}\nCall us for any query.",
         [*_BOOKING_EXAMPLE[:4], "Cancelled by you. ₹299 added to your wallet."], "View Booking", BOOKING_LINK,
         "{{5}} = who cancelled + the wallet line"),
    _def("blussit_captain_released_v5", "UTILITY",
         "🧑‍🔧 Captain unavailable\n🚗 {{1}} ({{2}})\n📅 {{3}} · {{4}}\n🚙 {{5}}\nA replacement captain will be assigned.\nCall us for any query.",
         _BOOKING_EXAMPLE, "View Booking", BOOKING_LINK),
    # -- money -----------------------------------------------------------
    _def("blussit_payment_confirmation_v4", "UTILITY",
         "✅ Payment received\n🚗 {{1}} ({{2}})\n💰 ₹{{3}}\nCall us for any query.",
         ["Star Wash", "BK-1042", "299"], "View Booking", BOOKING_LINK),
    _def("blussit_payment_pending_v4", "UTILITY",
         "💳 Payment pending\n🚗 {{1}} ({{2}})\n⏳ Pay within {{3}} minutes\nCall us for any query.",
         ["Star Wash", "BK-1042", "15"], "Complete Payment", BOOKING_LINK),
    _def("blussit_payment_failed_v1", "UTILITY",
         "❌ Payment Failed\n🚗 {{1}} ({{2}})\n💰 ₹{{3}}\nYour booking is not paid yet. Tap below to try again.\nCall us for any query.",
         ["Star Wash", "BK-1042", "299"], "Retry Payment", BOOKING_LINK),
    _def("blussit_wallet_credited_v1", "UTILITY",
         "Hi {{1}}, ₹{{2}} has been added to your Blussit wallet.\nReason: {{3}}\nWallet Balance: {{4}}\nCall us for any query.",
         ["Asha", "299", "Booking BK-1042 cancelled", "₹299"], "View Wallet", f"{SITE}/app/wallet"),
    _def("blussit_wallet_debited_v1", "UTILITY",
         "Hi {{1}}, ₹{{2}} has been deducted from your Blussit wallet.\nReason: {{3}}\nWallet Balance: {{4}}\nCall us for any query.",
         ["Asha", "50", "Late cancellation charge for BK-1042", "-₹50"], "View Wallet", f"{SITE}/app/wallet"),
    # -- plans -----------------------------------------------------------
    _def("blussit_subscription_payment_link_v1", "UTILITY",
         "💳 Pay ₹{{1}} to activate {{2}}:\n{{3}}\nCall us for any query.",
         ["999", "Monthly Pass (Hatchback)", "https://rzp.io/i/AbC123"]),
    _def("blussit_subscription_autopay_link_v1", "UTILITY",
         "🔄 Auto-pay ₹{{1}}/mo to activate {{2}}:\n{{3}}\nCall us for any query.",
         ["999", "Monthly Pass (Hatchback)", "https://rzp.io/i/AbC123"]),
    _def("blussit_subscription_activated", "UTILITY",
         "Hi {{1}} 👋\n\nYour BLUSSIT monthly pass is active.\n\nPlan: {{2}}\nVehicle: {{3}}\nWashes: {{4}}\nValid till: {{5}}\n\nYou can see it any time in your Blussit account.",
         ["Asha", "Monthly Pass", "Hatchback MP09AB1234", "4", "12 Nov 2026"], "View Plan", f"{SITE}/app/subscriptions"),
    _def("blussit_subscription_renewed", "UTILITY",
         "Hi {{1}} 👋\n\nYour BLUSSIT monthly pass has renewed.\n\nPlan: {{2}}\nValid till: {{3}}\n\nAuto-pay went through — nothing to do.",
         ["Asha", "Monthly Pass", "12 Nov 2026"], "View Plan", f"{SITE}/app/subscriptions"),
    _def("blussit_plan_expiring_v2", "UTILITY",
         "Hi {{1}}, your {{2}} ends on {{3}} with {{4}} washes left.\nTap below to view your plan.",
         ["Asha", "Monthly Pass", "12 Nov 2026", "2"], "View Plan", f"{SITE}/app/subscriptions"),
    # Meta filed this one as MARKETING — still the "N washes left" nudge
    # (pass_wash_reminder, sent wa_marketing) and plan_expiring's fallback.
    _def("blussit_subscription_expiring", "UTILITY",
         "Hi {{1}} 👋\n\nYour BLUSSIT pass ({{2}}) ends on {{3}} with {{4}} washes left.\n\nBook them before it ends, or renew to keep going.",
         ["Asha", "Monthly Pass", "12 Nov 2026", "2"], "Book Now", f"{SITE}/"),
    _def("blussit_subscription_expired", "UTILITY",
         "Hi {{1}} 👋\n\nYour BLUSSIT pass ({{2}}) has ended.\n\nRenew any time to keep your car shining.",
         ["Asha", "Monthly Pass"], "Renew Pass", f"{SITE}/app/subscriptions"),
    # -- staff -----------------------------------------------------------
    _def("blussit_manager_new_booking_v2", "UTILITY",
         "📥 New Booking\nCustomer: {{1}} ({{2}})\nVehicle: {{3}}\nService: {{4}}\nWhen: {{5}}\nArea: {{6}}\n\nPlease open the booking, check the details and assign a captain who is free for this slot.",
         ["Asha", "9876543210", "Hatchback", "Star Wash", "12 Oct 2026 · 9:00 AM – 12:00 PM", "Vijay Nagar, Indore"],
         "Open Bookings", f"{SITE}/manager/bookings"),
    # -- talking to one customer ------------------------------------------
    _def("blussit_universal_message_v1", "UTILITY",
         "Hi {{1}},\n{{2}}\n\nReply here if you need any help.\n— Team Blussit",
         ["Asha", "Your captain will reach about 15 minutes late today because of traffic."],
         note="Admin/manager writes {{2}} in the CRM (POST /notifications/universal-message)"),
    # Feedback on a specific completed booking = UTILITY under Meta's rules
    # (transaction-specific, no offer); if Meta re-files it MARKETING the
    # sweep respects opt-outs (see send_review_requests).
    _def("blussit_review_request_google_v1", "UTILITY",
         "Hi {{1}}, thanks for choosing Blussit for your {{2}}.\nHow did we do? Your Google review helps other car owners find us.\n— Team Blussit",
         ["Asha", "Star Wash"], "Rate Us On Google", GOOGLE_REVIEW_URL_SETTING,
         "Button = the admin's Google review URL at submit time (WhatsApp → Settings)"),
    # -- the generic fallback ("*{{1}}*\n{{2}}") ----------------------------
    _def("blussit_service_update_v3", "UTILITY", "*{{1}}*\n{{2}}\nCall us for any query.",
         ["Booking Update", "Your captain will reach by 10:30 AM."],
         note="Generic fallback — point WHATSAPP_UPDATE_TEMPLATE_NAME at the approved version"),
    # -- marketing: sent only to customers who haven't opted out ---------
    _def("blussit_book_now_v1", "MARKETING",
         "Hi {{1}}, is your car due for a wash? 🚗\nBook a Blussit doorstep wash in a minute — we come to you.",
         ["Asha"], "Book Now", f"{SITE}/book"),
    _def("blussit_repeat_booking", "MARKETING",
         "Hi {{1}} 👋\n\nReady for your next BLUSSIT car wash?\n\nBook a doorstep wash whenever your car needs it.",
         ["Asha"], "Book Now", f"{SITE}/"),
    _def("blussit_we_miss_you", "MARKETING",
         "Hi {{1}} 👋\n\nIt's been a while since your last BLUSSIT wash.\n\nYour car deserves a shine — we'll come to your doorstep whenever suits you.",
         ["Asha"], "Book Now", f"{SITE}/"),
]

# Replaced by a newer version above — never submitted again (the 10-07
# investigation found bootstrap still resubmitting them, and resubmitting
# REJECTED names unchanged, which Meta refuses).
SUPERSEDED_TEMPLATE_NAMES = (
    "blussit_booking_confirmed",
    "blussit_captain_assigned",
    "blussit_captain_on_the_way",
    "blussit_service_completed",
    "blussit_review_request",
    "blussit_booking_reminder",
    "blussit_reschedule_confirmation",
    "blussit_payment_confirmation",
    "blussit_booking_cancelled",
    "blussit_payment_pending",
    "blussit_captain_released",
    "blussit_manager_new_booking_v1",
)

_VERSION = re.compile(r"^(?P<base>.+?)_v(?P<n>\d+)$")
_LIVE_STATUSES = ("APPROVED", "PENDING", "IN_APPEAL", "PAUSED")


def split_version(name: str) -> tuple[str, int]:
    """("blussit_booking_edited", 1) for "blussit_booking_edited_v1"; an
    unversioned name is version 1 of itself."""
    m = _VERSION.match(name)
    return (m.group("base"), int(m.group("n"))) if m else (name, 1)


def template_key(name: str) -> str:
    """The catalogue key of a definition: its name without the version."""
    return split_version(name)[0]


def versioned_name(base: str, version: int) -> str:
    return f"{base}_v{version}"


def _events_for(name: str) -> list[str]:
    return sorted(e for e, n in EVENT_TEMPLATES.items() if n == name) + sorted(
        e for e, names in EVENT_TEMPLATE_FALLBACKS.items() if name in names
    )


async def _family_rows(db: AsyncIOMotorDatabase, name: str) -> list[dict]:
    """Rows of every version of `name`'s template at or above its own
    version, newest version first."""
    base, version = split_version(name)
    rows = await db.whatsapp_templates.find({"name": {"$regex": f"^{re.escape(base)}(_v[0-9]+)?$"}}).to_list(length=100)
    family = [r for r in rows if split_version(r["name"])[0] == base and split_version(r["name"])[1] >= version
              and (r["name"] == name or _VERSION.match(r["name"]))]
    return sorted(family, key=lambda r: split_version(r["name"])[1], reverse=True)


async def _submission_plan(db: AsyncIOMotorDatabase, d: dict) -> dict:
    """What submitting definition `d` would do now: "exists" (a live
    version is approved / in review) or "submit" with the name to use —
    the definition's own name, or the next version after a rejection."""
    family = await _family_rows(db, d["name"])
    live = next((r for r in family if r.get("status") in _LIVE_STATUSES and not r.get("disabled")), None)
    if live:
        return {"action": "exists", "name": live["name"], "status": live.get("status")}
    if not family:
        return {"action": "submit", "name": d["name"], "status": "NOT_SUBMITTED"}
    newest = family[0]
    base, version = split_version(newest["name"])
    if newest.get("status") in (None, "", "DRAFT") and newest["name"] == d["name"]:
        return {"action": "submit", "name": d["name"], "status": "NOT_SUBMITTED"}
    return {
        "action": "submit", "name": versioned_name(base, version + 1), "status": newest.get("status") or "UNKNOWN",
        "previous": newest["name"], "previous_reason": newest.get("rejected_reason"),
    }


async def _resolve_button_url(db: AsyncIOMotorDatabase, d: dict) -> str | None:
    if d.get("button_url") != GOOGLE_REVIEW_URL_SETTING:
        return d.get("button_url")
    return (await WhatsAppCrmService(db).get_settings()).get("google_review_url") or None


async def submit_template_def(db: AsyncIOMotorDatabase, d: dict) -> dict:
    """Submit one catalogue definition (or report why not). Never raises
    for a Meta refusal — the result says ERROR with Meta's message."""
    plan = await _submission_plan(db, d)
    if plan["action"] == "exists":
        return {"name": plan["name"], "status": plan["status"], "note": "already exists"}
    button_url = await _resolve_button_url(db, d)
    if d.get("button_text") and not button_url:
        return {"name": plan["name"], "status": "SKIPPED",
                "note": "Set the Google review URL first (WhatsApp → Settings) — it is the template's button"}
    try:
        result = await WhatsAppCrmService(db).create_template(
            plan["name"], d["category"], "en_US", d["body"], d.get("button_text"), button_url, examples=d["examples"],
        )
    except BadRequestException as exc:
        return {"name": plan["name"], "status": "ERROR", "note": exc.message}
    note = d.get("note") or ""
    if plan.get("previous"):
        reason = plan.get("previous_reason") or plan["status"]
        note = (f"{plan['previous']} was {plan['status']} ({reason}) — submitted as {plan['name']}. "
                "If the reason was the wording, change the copy in code first. " + note).strip()
    result["note"] = note or "submitted"
    return result


async def bootstrap_blussit_templates(db: AsyncIOMotorDatabase) -> list[dict]:
    """Submits every CURRENT template that isn't approved / in review yet
    (rejected ones as their next version) plus the OTP template. Superseded
    legacy templates are never submitted."""
    crm = WhatsAppCrmService(db)
    await crm.sync_templates()
    results = []
    otp_name = "blussit_otp"
    existing_otp = await db.whatsapp_templates.find_one({"name": otp_name})
    if existing_otp and existing_otp.get("status") in ("APPROVED", "PENDING"):
        results.append({"name": otp_name, "status": existing_otp["status"], "note": "already exists"})
    else:
        try:
            r = await crm.create_otp_template(otp_name)
            r["note"] = "AUTHENTICATION template with a Copy Code button — point WHATSAPP_OTP_TEMPLATE_NAME at this once approved"
            results.append(r)
        except BadRequestException as exc:
            results.append({"name": otp_name, "status": "ERROR", "note": exc.message})
    for d in CURRENT_TEMPLATE_DEFS:
        results.append(await submit_template_def(db, d))
    return results
