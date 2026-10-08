import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException, TooManyRequestsException
from app.models.enums import NotificationType
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository
from app.services.whatsapp_service import WhatsAppService, last_complete_template_sync, mask_phone
from app.utils.phone import is_business_whatsapp_number
from app.utils.serializers import serialize_doc, serialize_list
from app.utils.timezone import from_stored, in_customer_message_hours, now_ist, to_ist

logger = logging.getLogger(__name__)

# asyncio only holds a WEAK reference to a task it isn't awaited/stored
# somewhere — without this, a fire-and-forget notify(background=True) task
# can get garbage-collected mid-flight before Meta's API even responds.
# Keeping a strong reference here (and dropping it on completion) is the
# standard fix.
_background_tasks: set[asyncio.Task] = set()

# The only notifications a manager receives on WhatsApp; everything else
# addressed to a manager is written in-app only. Also the events a
# recipient's "WhatsApp me new bookings" switch (users.whatsapp_new_booking_alerts,
# on when missing) turns off.
MANAGER_WHATSAPP_EVENTS = frozenset({"manager_new_booking", "manager_new_booking_v2"})
NEW_BOOKING_ALERTS_FIELD = "whatsapp_new_booking_alerts"

# Events that go out ONLY through their own approved template — never the
# generic utility fallback (a review ask or an offer through the update
# template would be marketing wearing a utility badge). Marketing calls
# (wa_marketing=True) behave the same way.
TEMPLATE_ONLY_EVENTS = frozenset({"review_request_google", "book_now"})

# Manager alerts that ask for an action, keyed by how their title starts.
# Once the action is done the alert clears itself (see clear_handled_alerts).
NEEDS_CAPTAIN_TITLES = ("New booking", "Still needs a captain")
URGENT_TITLE = "🚨 Urgent"
FINISHED_BOOKING = frozenset({"completed", "cancelled"})
CLOSED_COMPLAINT = frozenset({"resolved", "closed"})
CLEAR_SCAN_LIMIT = 300

# ---------------------------------------------------------------------------
# WhatsApp delivery queue (FAIL-01/02). Every WhatsApp half of a notify() is
# a row in `whatsapp_queue` BEFORE it is sent, so a send that fails at the
# transport level (Meta down, timeout, pool exhausted, process restart) is
# retried by retry_outbox() instead of being lost, and a caller that
# retries can't message the customer twice (the row id is the idempotency
# key). `whatsapp_outbox` stays what it was: the log of every actual send.
#
# Row statuses: sending (an attempt holds the lease) → sent | pending (a
# transient failure; retried at next_at, exponential backoff) | failed (a
# permanent refusal — Meta rejected it, no approved template, opted out) |
# dead (gave up: QUEUE_MAX_ATTEMPTS transient failures or older than
# QUEUE_MAX_AGE — a "captain on the way" hours late is worse than none).
# ---------------------------------------------------------------------------
QUEUE_COLLECTION = "whatsapp_queue"
# A request path (assign, reschedule, captain steps) waits at most this long
# for its WhatsApp send; a slower send finishes in the background and the
# queue row guarantees it is retried if it never does. A healthy Meta call
# (and the log provider) is well inside it, so most sends complete inline.
INLINE_SEND_WAIT_SECONDS = 1.0
QUEUE_LEASE_SECONDS = 120  # > one attempt's worst case (two 10 s Meta calls + DB)
QUEUE_MAX_ATTEMPTS = 7
QUEUE_BACKOFF_SECONDS = (60, 120, 240, 480, 960, 1800)  # after attempt 1, 2, …
QUEUE_MAX_AGE = timedelta(hours=3)
# The same message (same event, booking, person and content) inside this
# window is the same send retried by its caller — enqueued and sent once.
QUEUE_DEDUPE_WINDOW = timedelta(minutes=10)
QUEUE_RETENTION_DAYS = 30
# Meta refusing OUR credentials (401/403): every send fails the same way
# until the token is fixed, so it's retried only a few times, slowly, and
# then dead-lettered (delivery health shows the config warning).
AUTH_MAX_ATTEMPTS = 3
AUTH_BACKOFF_SECONDS = (300, 900)
# "undelivered": Meta accepted it (HTTP 200), then the status webhook
# reported the delivery failed (not on WhatsApp, 24 h window, …).
_TERMINAL = ("sent", "failed", "dead", "undelivered")
_FAILED_STATUSES = ("failed", "dead", "undelivered")
_indexed_dbs: set[str] = set()

# Review requests (spec 1.8): ~2 h after a completed AND fully paid visit,
# 10 AM–7 PM IST, once per visit; at most one Google-review ask per
# customer per REVIEW_REQUEST_COOLDOWN_DAYS (default — a weekly plan
# customer isn't asked every week). Visits older than MAX_AGE are never
# asked (the sweep was down, or the template got approved later).
REVIEW_REQUEST_DELAY = timedelta(hours=2)
REVIEW_REQUEST_MAX_AGE = timedelta(hours=48)
REVIEW_REQUEST_COOLDOWN_DAYS = 30
# (The pre-slot customer reminder — event "booking_reminder" — is
# BookingService.find_bookings_needing_customer_reminder / send_customer_reminder.)
CLAIMS_COLLECTION = "notification_claims"
SWEEP_LIMIT = 200
UNIVERSAL_MESSAGE_MAX = 500
# At most this many universal messages reach one customer per rolling 24 h,
# from all staff together (security review 2026-10-07: free text under the
# Blussit name, each a paid Meta conversation — never a flood).
UNIVERSAL_MESSAGE_DAILY_CAP = 5


def _fire_and_forget(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _queue_key(user_id: str, title: str, message: str, reference_id: str | None, wa_event: str | None, wa_params, wa_marketing: bool) -> str:
    """(event, booking, recipient, content) — the content is part of it so
    a genuinely new message about the same booking (a reassignment to
    another captain, a second reschedule) is never mistaken for a retry."""
    content = json.dumps([title, message, wa_params, bool(wa_marketing)], default=str, ensure_ascii=False)
    digest = hashlib.sha256(content.encode()).hexdigest()[:20]
    return f"{wa_event or 'notify'}:{reference_id or '-'}:{user_id}:{digest}"


async def ensure_queue_indexes(db: AsyncIOMotorDatabase) -> None:
    """Idempotent; once per process (retry_outbox calls it)."""
    if db.name in _indexed_dbs:
        return
    queue = db[QUEUE_COLLECTION]
    await queue.create_index([("status", 1), ("next_at", 1)])
    await queue.create_index([("status", 1), ("lease_until", 1)])
    await queue.create_index("created_at", expireAfterSeconds=QUEUE_RETENTION_DAYS * 24 * 3600)
    _indexed_dbs.add(db.name)


@dataclass(frozen=True)
class _Result:
    status: str  # sent | retry | failed
    failure: str | None = None
    error: str | None = None
    channel: str | None = None
    template_name: str | None = None


# Customers whose WhatsApp half is muted for the code running inside
# `whatsapp_muted_for()` — the WhatsApp bot creating a booking answers in
# the chat itself ("🎉 Booking confirmed!" with the code and pay buttons),
# so the template copy of the same announcement would be a duplicate.
# Same ContextVar scoping as app.utils.quiet_alerts: only that block is
# affected, the in-app row is still written, other recipients untouched.
_whatsapp_muted: ContextVar[frozenset] = ContextVar("whatsapp_muted_for", default=frozenset())


@contextmanager
def whatsapp_muted_for(user_id: str):
    token = _whatsapp_muted.set(_whatsapp_muted.get() | {str(user_id)})
    try:
        yield
    finally:
        _whatsapp_muted.reset(token)


class NotificationService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.repo = NotificationRepository(db)
        # Every in-app notification also goes out on WhatsApp when the
        # recipient has a phone on file — a single bridge point here means
        # every one of this app's ~30 existing notify() call sites (booking
        # updates, complaint replies, assignment changes, etc.) gets
        # WhatsApp delivery for free, with no per-call-site changes needed.
        self.user_repo = UserRepository(db)
        self.whatsapp = WhatsAppService(db)
        self.queue = db[QUEUE_COLLECTION]

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
        background: bool | None = None,
        send_whatsapp: bool = True,
    ) -> None:
        """`wa_marketing`: the WhatsApp half is a MARKETING template ("book
        again", "we miss you"). Those go out only through their approved
        template and only to customers who haven't opted out — never via
        the generic utility fallback, which would be spam wearing a
        utility badge. The in-app notification is written regardless.

        The WhatsApp half is queued durably (whatsapp_queue) before it is
        sent — a transport failure is retried by retry_outbox(), and the
        same call made twice sends once. `background`:
          None (default, request paths): wait for the send at most
            INLINE_SEND_WAIT_SECONDS, then let it finish in the background
            — Meta hanging never holds an assign/reschedule/captain step;
          True: don't wait at all (the caller's response must render
            before Meta even answers — a booking just confirmed);
          False: wait for the whole send (the WhatsApp bot — its own chat
            reply right after must not overtake the announcement).
        Either way the in-app row is written before this returns, so
        anything reading it immediately after (a socket push, a test) sees
        it.

        `send_whatsapp=False`: write the in-app row only (a manager marking
        a job done with the "notify on WhatsApp" switch off)."""
        kind = notification_type.value if hasattr(notification_type, "value") else notification_type
        await self.repo.create(
            {
                "user_id": user_id,
                "title": title,
                "message": message,
                "notification_type": kind,
                "reference_id": reference_id,
                "is_read": False,
            }
        )
        user = await self.user_repo.find_by_id(user_id)
        phone = (user or {}).get("phone")
        if wa_marketing and (user or {}).get("marketing_opt_out"):
            return
        if (user or {}).get("role") == "manager" and wa_event not in MANAGER_WHATSAPP_EVENTS:
            # Founder's rule: a manager's WhatsApp carries new bookings
            # only — every other alert (reminders, late starts, flags,
            # complaints) stays in the portal's bell.
            send_whatsapp = False
        if wa_event in MANAGER_WHATSAPP_EVENTS and (user or {}).get(NEW_BOOKING_ALERTS_FIELD) is False:
            # The recipient switched "WhatsApp me new bookings" off — the
            # bell still has it.
            send_whatsapp = False
        if str(user_id) in _whatsapp_muted.get():
            send_whatsapp = False
        if not send_whatsapp:
            return
        args = (str(user_id), phone, title, message, kind, reference_id, wa_event, wa_params, wa_marketing)
        if not phone:
            # Used to be a silent return: a manager with no phone simply
            # never heard about new bookings and nothing anywhere said so.
            await self._enqueue(*args, failed=("no_recipient", "No phone number on the account"), role=(user or {}).get("role"))
            return
        if is_business_whatsapp_number(phone):
            await self._enqueue(
                *args, failed=("business_number", "The phone is the business's own WhatsApp number — Meta refuses it"),
                role=(user or {}).get("role"),
            )
            return
        row = await self._enqueue(*args)
        if row is None:
            return  # the same message is already queued or was just sent
        coro = self._send_whatsapp(
            user_id, phone, title, message, wa_event, wa_params, wa_marketing, reference_id, queue_row=row, user=user
        )
        if background is False:
            await coro
        elif background:
            _fire_and_forget(coro)
        else:
            await asyncio.wait({_fire_and_forget(coro)}, timeout=INLINE_SEND_WAIT_SECONDS)

    async def _enqueue(
        self, user_id: str, phone: str | None, title: str, message: str, kind: str | None, reference_id: str | None,
        wa_event: str | None, wa_params: list | None, wa_marketing: bool,
        failed: tuple[str, str] | None = None, role: str | None = None,
    ) -> dict | None:
        """Writes the queue row already leased to this attempt. None = the
        same message was enqueued within QUEUE_DEDUPE_WINDOW (or is still
        being retried) — a duplicate call, sent once. A DB failure here
        never blocks the send itself: the attempt goes ahead un-queued.

        `failed=(reason, error)`: the message can't be sent at all (no
        phone, the business's own number) — the row is written already
        failed, so delivery health counts it instead of it vanishing."""
        now = _utcnow()
        key = _queue_key(user_id, title, message, reference_id, wa_event, wa_params, wa_marketing)
        if failed:
            # Staff missing an alert is a configuration problem; a customer
            # without a phone (Google sign-in) is ordinary — counted, quiet.
            log = logger.error if role in ("manager", "admin", "captain") else logger.info
            log("WhatsApp message not sendable (%s: %s) — user=%s (%s), to=%s, title=%r; delivered in-app only",
                failed[0], failed[1], user_id, role or "?", mask_phone(phone), title)
        doc = {
            "_id": key,
            "user_id": user_id,
            "phone": phone,
            "title": title,
            "message": message,
            "notification_type": kind,
            "reference_id": reference_id,
            "wa_event": wa_event,
            "wa_params": wa_params,
            "wa_marketing": bool(wa_marketing),
            "status": "sending",
            "attempts": 0,
            "lease_owner": uuid.uuid4().hex,
            "lease_until": now + timedelta(seconds=QUEUE_LEASE_SECONDS),
            "next_at": now,
            "created_at": now,
            "updated_at": now,
        }
        if failed:
            doc.update({"status": "failed", "failure": failed[0], "last_error": failed[1], "lease_owner": None, "lease_until": None})
        try:
            await self.queue.insert_one(doc)
            return doc
        except DuplicateKeyError:
            pass
        except Exception:  # noqa: BLE001 — the queue is a safety net, not a gate
            if failed:
                logger.exception("WhatsApp queue write failed (user=%s)", user_id)
                return None
            logger.exception("WhatsApp queue write failed (user=%s) — sending without a retry safety net", user_id)
            return {**doc, "_volatile": True}
        # The same message long after the last one finished is a new send
        # (taken over atomically: two racing callers can't both).
        try:
            previous = await self.queue.find_one_and_replace(
                {"_id": key, "status": {"$in": list(_TERMINAL)}, "created_at": {"$lt": now - QUEUE_DEDUPE_WINDOW}}, doc
            )
        except Exception:  # noqa: BLE001 — can't tell: never risk a double send
            logger.exception("WhatsApp queue re-send check failed (user=%s) — skipped", user_id)
            return None
        return doc if previous else None

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
        *,
        queue_row: dict | None = None,
        user: dict | None = None,
    ) -> str:
        """One delivery attempt of a queued message; records the outcome on
        its row and returns the row's new status. Never raises — this may be
        running unawaited in the background, and a WhatsApp failure must
        never break the caller's business action."""
        row = queue_row or {
            "_id": None, "_volatile": True, "user_id": str(user_id), "phone": phone, "title": title, "message": message,
            "notification_type": None, "reference_id": reference_id, "wa_event": wa_event, "wa_params": wa_params,
            "wa_marketing": bool(wa_marketing), "attempts": 0, "created_at": _utcnow(),
        }
        try:
            result = await self._deliver(row, user)
        except Exception as exc:  # noqa: BLE001
            logger.exception("WhatsApp delivery attempt crashed (user=%s, title=%r)", row.get("user_id"), row.get("title"))
            result = _Result("retry", "error", repr(exc)[:300])
        try:
            return await self._settle(row, result)
        except Exception:  # noqa: BLE001
            logger.exception("Could not record the WhatsApp delivery outcome (queue=%s)", row.get("_id"))
            return "pending"

    async def _deliver(self, row: dict, user: dict | None) -> _Result:
        # Automation engine: rows that name a wa_event get the dedicated
        # per-event template (blussit_booking_confirmed, ...) as soon as
        # Meta approves it; until then — or if Meta REJECTS the event send —
        # the generic update template carries the same information, so
        # nothing goes dark while templates sit in review. A TRANSPORT
        # failure (Meta unreachable) is retried later instead: the generic
        # send would only block on the same dead API a second time.
        retry_attempt = user is None
        if retry_attempt:
            user = await self.user_repo.find_by_id(row["user_id"])
            if not user or user.get("is_deleted") or not user.get("phone"):
                return _Result("failed", "no_recipient", "The account or its phone number is gone")
            if row.get("wa_marketing") and user.get("marketing_opt_out"):
                return _Result("failed", "opted_out", "The customer opted out of marketing messages")
        phone = (user or {}).get("phone") or row["phone"]
        if retry_attempt and await self._already_delivered(row, phone):
            # The previous holder sent it and died before recording that.
            return _Result("sent", channel="recovered")
        queue_id = None if row.get("_volatile") else row["_id"]
        wa_event, wa_params = row.get("wa_event"), row.get("wa_params")
        opted_out = bool((user or {}).get("marketing_opt_out"))
        event_failure = None
        if wa_event and wa_params is not None:
            try:
                from app.services.whatsapp_crm_service import WhatsAppCrmService

                tpl = await WhatsAppCrmService(self.db).event_template_if_ready(wa_event)
                if tpl and tpl.get("category") == "MARKETING" and opted_out:
                    # Meta filed (or re-filed) this template as MARKETING:
                    # it may not reach a customer who opted out — whatever
                    # the call site thought it was.
                    event_failure = "opted_out"
                elif tpl:
                    # Only a template actually approved WITH a URL
                    # variable on its button gets one filled in — see
                    # create_template's has_url_param. reference_id is
                    # always the booking id for every event that has
                    # one of these (see CURRENT_TEMPLATE_DEFS), which
                    # is exactly what "/app/bookings/{{1}}" expects.
                    button_param = None
                    reference_id = row.get("reference_id")
                    if tpl.get("has_url_param") and reference_id:
                        button_param = f"{reference_id}?review=1" if wa_event == "service_completed" else reference_id
                    # Templates can be shorter than the params a call site builds (the
                    # service code was dropped from the _v5 bodies) — send exactly
                    # as many as the approved template declares.
                    expected = tpl.get("param_count")
                    params = [str(p) for p in (wa_params if expected is None else wa_params[:expected])]
                    outcome = await self.whatsapp.send_event_template_outcome(
                        phone, tpl["name"], params, button_param=button_param, queue_id=queue_id, language=tpl.get("language"),
                    )
                    if outcome.ok:
                        return _Result("sent", channel="event_template", template_name=tpl["name"])
                    if outcome.transport_failure:
                        return _Result("retry", "transport", "Meta was unreachable (event template)", template_name=tpl["name"])
                    if outcome.reason == "auth":
                        # The generic would fail on the same credentials.
                        return _Result("retry", "auth", "Meta refused our WhatsApp credentials (401/403)", template_name=tpl["name"])
                    if outcome.reason == "business_number":
                        return _Result("failed", "business_number", "The phone is the business's own WhatsApp number")
                    event_failure = "rejected"
            except Exception:  # noqa: BLE001 — automation must never block the fallback
                logger.exception("WhatsApp event-template send failed (event=%s, user=%s) — falling back to generic", wa_event, row.get("user_id"))
        if row.get("wa_marketing") or wa_event in TEMPLATE_ONLY_EVENTS:
            # Approved template or nothing — no utility fallback.
            if event_failure == "opted_out":
                return _Result("failed", "opted_out", "The template is MARKETING and the customer opted out")
            if event_failure:
                return _Result("failed", "rejected", "Meta refused the template")
            return _Result("failed", "no_template", "The template is not approved")
        outcome = await self.whatsapp.send_generic_outcome(
            phone, row["title"], row["message"], row.get("notification_type"), queue_id=queue_id, marketing_opt_out=opted_out,
        )
        if outcome.ok:
            return _Result("sent", channel="generic")
        if outcome.transport_failure:
            return _Result("retry", "transport", "Meta was unreachable (generic update)")
        if outcome.reason == "auth":
            return _Result("retry", "auth", "Meta refused our WhatsApp credentials (401/403)")
        if outcome.reason == "business_number":
            return _Result("failed", "business_number", "The phone is the business's own WhatsApp number")
        if outcome.reason == "opted_out":
            return _Result("failed", "opted_out", "The update template is MARKETING and the customer opted out")
        if outcome.reason == "no_template":
            return _Result("failed", "no_template", "No approved update template and the 24-hour window is closed")
        return _Result("failed", "rejected", "Meta refused the message")

    async def _already_delivered(self, row: dict, phone: str) -> bool:
        if row.get("_volatile") or not row.get("_id"):
            return False
        found = await self.db.whatsapp_outbox.find_one(
            {
                "phone": {"$in": list({phone, row.get("phone")})},
                "created_at": {"$gte": row.get("created_at") or _utcnow()},
                "queue_id": row["_id"],
                "ok": {"$ne": False},
            },
            {"_id": 1},
        )
        return found is not None

    async def _settle(self, row: dict, result: _Result) -> str:
        now = _utcnow()
        attempts = int(row.get("attempts") or 0) + 1
        update: dict = {"attempts": attempts, "updated_at": now, "lease_owner": None, "lease_until": None}
        if result.status == "sent":
            status = "sent"
            update.update({"sent_at": now, "channel": result.channel, "template_name": result.template_name, "failure": None})
        elif result.status == "retry":
            age = now - (_aware(row.get("created_at")) or now)
            auth = result.failure == "auth"
            max_attempts, backoff = (AUTH_MAX_ATTEMPTS, AUTH_BACKOFF_SECONDS) if auth else (QUEUE_MAX_ATTEMPTS, QUEUE_BACKOFF_SECONDS)
            if attempts >= max_attempts or age >= QUEUE_MAX_AGE:
                status = "dead"
            else:
                status = "pending"
                delay = backoff[min(attempts, len(backoff)) - 1]
                update["next_at"] = now + timedelta(seconds=delay)
            update.update({"failure": result.failure, "last_error": result.error})
        else:
            status = "failed"
            update.update({"failure": result.failure, "last_error": result.error})
        update["status"] = status
        who = f"user={row.get('user_id')}, to={mask_phone(row.get('phone'))}, title={row.get('title')!r}"
        if result.failure == "auth":
            # A configuration problem, not this message: ERROR every time.
            logger.error("WhatsApp credentials refused by Meta — message %s (attempt %d) — %s", status, attempts, who)
        elif status in ("failed", "dead"):
            # LOUD — expired Meta credentials used to silently stop every
            # customer message while the in-app rows kept dashboards green.
            logger.warning("WhatsApp message %s (%s: %s) — %s; delivered in-app only", status, result.failure, result.error, who)
        elif status == "pending":
            logger.info("WhatsApp send failed (%s), retry %d at %s — %s", result.failure, attempts, update["next_at"].isoformat(), who)
        if not row.get("_volatile") and row.get("_id") is not None:
            # Guarded on the lease: a worker that lost it (expired, re-claimed)
            # doesn't overwrite the new holder's outcome.
            await self.queue.update_one({"_id": row["_id"], "lease_owner": row.get("lease_owner")}, {"$set": update})
        return status

    async def retry_outbox(self, limit: int = 100, time_budget_seconds: float = 25.0) -> dict:
        """Retries due WhatsApp sends: rows waiting out their backoff, and
        rows whose sender died mid-attempt (lease expired). Each row is
        claimed with an atomic lease, so any number of instances can run
        this at once and a row is still sent by exactly one. Idempotent;
        safe to call every loop tick. Returns counts by outcome."""
        await ensure_queue_indexes(self.db)
        owner = uuid.uuid4().hex
        stats = {"claimed": 0, "sent": 0, "pending": 0, "failed": 0, "dead": 0}
        deadline = time.monotonic() + time_budget_seconds
        while stats["claimed"] < limit and time.monotonic() < deadline:
            now = _utcnow()
            row = await self.queue.find_one_and_update(
                {"$or": [{"status": "pending", "next_at": {"$lte": now}}, {"status": "sending", "lease_until": {"$lt": now}}]},
                {"$set": {"status": "sending", "lease_owner": owner, "lease_until": now + timedelta(seconds=QUEUE_LEASE_SECONDS), "updated_at": now}},
                sort=[("next_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if row is None:
                break
            stats["claimed"] += 1
            status = await self._send_whatsapp(
                row["user_id"], row.get("phone"), row.get("title"), row.get("message"), row.get("wa_event"),
                row.get("wa_params"), row.get("wa_marketing", False), row.get("reference_id"), queue_row=row,
            )
            stats[status or "pending"] = stats.get(status or "pending", 0) + 1
        if stats["claimed"]:
            logger.info("WhatsApp retry pass: %s", stats)
        return stats

    async def delivery_health(self, days: int = 7) -> dict:
        """What did NOT reach people on WhatsApp — for the admin panel:
        queue rows by status, failures by reason, free text refused outside
        the 24-hour window, and the latest failures (phones masked)."""
        since = _utcnow() - timedelta(days=days)
        queue = {status: 0 for status in ("pending", "sending", "sent", "failed", "dead", "undelivered")}
        failures: dict[str, int] = {}
        async for group in self.queue.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": {"status": "$status", "failure": "$failure"}, "n": {"$sum": 1}}},
        ]):
            status, failure = group["_id"].get("status"), group["_id"].get("failure")
            queue[status] = queue.get(status, 0) + group["n"]
            if status in _FAILED_STATUSES and failure:
                failures[failure] = failures.get(failure, 0) + group["n"]
        refused = await self.db.whatsapp_outbox.count_documents(
            {"created_at": {"$gte": since}, "failure": {"$in": ["no_template", "window_closed"]}}
        )
        recent = await self.queue.find(
            {"status": {"$in": list(_FAILED_STATUSES)}, "created_at": {"$gte": since}},
            {"user_id": 1, "phone": 1, "title": 1, "wa_event": 1, "status": 1, "failure": 1, "last_error": 1, "attempts": 1, "created_at": 1, "updated_at": 1},
        ).sort("updated_at", -1).limit(20).to_list(20)
        return {
            "days": days,
            "queue": queue,
            "failures_by_reason": failures,
            "refused_outside_window": refused,
            "config_warnings": await self.config_warnings(),
            "recent_failures": [
                {
                    "id": r["_id"], "user_id": r.get("user_id"), "phone": mask_phone(r.get("phone")), "title": r.get("title"),
                    "event": r.get("wa_event"), "status": r.get("status"), "failure": r.get("failure"),
                    "error": r.get("last_error"), "attempts": r.get("attempts"),
                    "at": (_aware(r.get("updated_at")) or _aware(r.get("created_at"))).isoformat() if (r.get("updated_at") or r.get("created_at")) else None,
                }
                for r in recent
            ],
        }

    # ------------------------------------------------------------------
    # Delivery receipts (status webhook) → the queue
    # ------------------------------------------------------------------

    async def apply_delivery_status(self, wamid: str, status: str | None, errors: list[dict] | None = None) -> None:
        """Meta's later verdict on a message it accepted. A 200 from the
        send API only means "accepted": a `failed` status (not on WhatsApp,
        window closed, spam limits…) used to leave the queue row "sent" and
        delivery health green. Never raises (webhook path)."""
        try:
            out = await self.db.whatsapp_outbox.find_one({"wamid": wamid}, {"queue_id": 1})
            queue_id = (out or {}).get("queue_id")
            if not queue_id:
                return
            now = _utcnow()
            if status == "failed":
                first = (errors or [{}])[0] or {}
                error = " ".join(str(x) for x in (first.get("code"), first.get("title"), first.get("message")) if x) or "Delivery failed"
                result = await self.queue.update_one(
                    {"_id": queue_id, "status": "sent"},
                    {"$set": {"status": "undelivered", "failure": "undelivered", "last_error": error[:300],
                              "delivery_status": "failed", "updated_at": now}},
                )
                if result.modified_count:
                    row = await self.queue.find_one({"_id": queue_id}, {"user_id": 1, "phone": 1, "title": 1})
                    logger.warning(
                        "WhatsApp message undelivered (%s) — user=%s, to=%s, title=%r",
                        error[:120], (row or {}).get("user_id"), mask_phone((row or {}).get("phone")), (row or {}).get("title"),
                    )
            elif status in ("delivered", "read"):
                guard = {"_id": queue_id, "status": "sent"}
                if status == "delivered":
                    guard["delivery_status"] = {"$ne": "read"}
                await self.queue.update_one(guard, {"$set": {"delivery_status": status, f"{status}_at": now}})
        except Exception:  # noqa: BLE001
            logger.exception("Could not apply WhatsApp delivery status %s to the queue", status)

    # ------------------------------------------------------------------
    # Configuration warnings (admin delivery-health panel)
    # ------------------------------------------------------------------

    async def config_warnings(self) -> list[dict]:
        """Why WhatsApp would NOT reach people, before anyone notices:
        managers who can't get new-booking alerts, templates that aren't
        usable, Meta refusing our credentials, templates Meta re-filed as
        MARKETING. [{severity: error|warning|info, code, message, …}]."""
        from app.core.authz import account_switched_off
        from app.services.whatsapp_crm_service import EVENT_TEMPLATES, WhatsAppCrmService

        warnings: list[dict] = []

        def add(severity: str, code: str, message: str, **extra) -> None:
            warnings.append({"severity": severity, "code": code, "message": message, **extra})

        managers = await self.db.users.find(
            {"role": "manager", "is_deleted": {"$ne": True}},
            {"full_name": 1, "phone": 1, "phone_verified": 1, "status": 1, "service_center_id": 1, NEW_BOOKING_ALERTS_FIELD: 1},
        ).limit(500).to_list(500)
        for m in managers:
            if account_switched_off(m):
                continue
            who = {"user_id": str(m["_id"]), "name": m.get("full_name"), "service_center_id": m.get("service_center_id")}
            phone = m.get("phone")
            if not phone:
                add("error", "manager_no_phone", f"{m.get('full_name') or 'A manager'} has no phone — new-booking WhatsApps can't reach them.", **who)
            elif is_business_whatsapp_number(phone):
                add("error", "manager_business_number",
                    f"{m.get('full_name') or 'A manager'}'s phone is the business's own WhatsApp number — Meta refuses every message to it.",
                    phone=mask_phone(phone), **who)
            elif not m.get("phone_verified"):
                add("warning", "manager_phone_unverified",
                    f"{m.get('full_name') or 'A manager'}'s phone isn't verified — check it is their real WhatsApp number.",
                    phone=mask_phone(phone), **who)
            if m.get(NEW_BOOKING_ALERTS_FIELD) is False:
                add("info", "manager_alerts_off", f"{m.get('full_name') or 'A manager'} switched new-booking WhatsApps off.", **who)

        crm = WhatsAppCrmService(self.db)
        generic = await self.whatsapp.update_template()
        generic_name = settings.WHATSAPP_UPDATE_TEMPLATE_NAME
        synced = await last_complete_template_sync(self.db)
        if not generic:
            if generic_name and synced and not await self.db.whatsapp_templates.find_one({"name": generic_name}, {"_id": 1}):
                add("error", "template_unknown",
                    f"WHATSAPP_UPDATE_TEMPLATE_NAME '{generic_name}' isn't on Meta (last template sync) — check the name.",
                    template=generic_name)
            add("error", "generic_template_not_approved",
                (f"The generic update template '{generic_name}' isn't approved" if generic_name else "WHATSAPP_UPDATE_TEMPLATE_NAME is not set")
                + " — messages without their own approved template only reach customers who chatted in the last 24 h.",
                template=generic_name or None)
        for setting_name in ("WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME",):
            name = getattr(settings, setting_name, "")
            if name and synced and not await self.db.whatsapp_templates.find_one({"name": name}, {"_id": 1}):
                add("error", "template_unknown", f"{setting_name} '{name}' isn't on Meta (last template sync) — check the name.", template=name)
        if not await crm.event_template_if_ready("manager_new_booking"):
            add("warning" if generic else "error", "manager_template_not_approved",
                f"The manager new-booking template ({EVENT_TEMPLATES['manager_new_booking']}) isn't approved — "
                + ("alerts go out through the generic update template." if generic else "and neither is the generic one: managers get NO WhatsApp."),
                template=EVENT_TEMPLATES["manager_new_booking"])

        day_ago = _utcnow() - timedelta(days=1)
        auth_rows = await self.queue.count_documents({"failure": "auth", "updated_at": {"$gte": day_ago}})
        auth_rows += await self.db.whatsapp_outbox.count_documents({"failure": "auth", "created_at": {"$gte": day_ago}})
        if auth_rows:
            add("error", "whatsapp_auth_failed",
                f"Meta refused our WhatsApp credentials {auth_rows} time(s) in the last 24 h — renew WHATSAPP_ACCESS_TOKEN.",
                count=auth_rows)

        utility_names = {n for e, n in EVENT_TEMPLATES.items() if e not in ("repeat_booking", "we_miss_you", "book_now", "pass_wash_reminder")}
        recategorised = await self.db.whatsapp_templates.find(
            {"name": {"$in": sorted(utility_names | ({generic_name} if generic_name else set()))}, "category": "MARKETING"},
            {"name": 1},
        ).to_list(100)
        for t in recategorised:
            add("warning", "template_recategorised",
                f"Meta files '{t['name']}' as MARKETING — it no longer reaches customers who opted out of offers.",
                template=t["name"])

        review_url = (await crm.get_settings()).get("google_review_url")
        review = await crm.event_template_if_ready("review_request_google")
        if review and review_url and review.get("button_url") and review["button_url"] != review_url:
            add("warning", "review_url_changed",
                "The Google review URL changed since the review template was approved — submit a new version so the button opens the new link.",
                template=review["name"])
        return warnings

    # ------------------------------------------------------------------
    # Preferences: the manager's "WhatsApp me new bookings" switch
    # ------------------------------------------------------------------

    async def get_preferences(self, user_id: str) -> dict:
        user = await self.user_repo.find_by_id(user_id)
        if not user:
            raise NotFoundException("User not found")
        return {"user_id": user_id, NEW_BOOKING_ALERTS_FIELD: user.get(NEW_BOOKING_ALERTS_FIELD) is not False}

    async def set_preferences(self, actor_id: str, actor_role: str, enabled: bool, user_id: str | None = None) -> dict:
        """Managers (and admins) switch their OWN new-booking WhatsApps;
        only an admin may switch someone else's (a manager or admin)."""
        target_id = user_id or actor_id
        if actor_role not in ("manager", "admin"):
            raise ForbiddenException("Only managers and admins have WhatsApp alert settings")
        if target_id != actor_id and actor_role != "admin":
            raise ForbiddenException("You can only change your own alerts")
        target = await self.user_repo.find_by_id(target_id)
        if not target or target.get("role") not in ("manager", "admin"):
            raise NotFoundException("User not found")
        await self.db.users.update_one(
            {"_id": ObjectId(target_id)}, {"$set": {NEW_BOOKING_ALERTS_FIELD: bool(enabled), "updated_at": _utcnow()}}
        )
        from app.services.audit_service import AuditService

        await AuditService(self.db).log_action(
            actor_id, actor_role, "UPDATE_NOTIFICATION_PREFERENCES", "users", target_id, {NEW_BOOKING_ALERTS_FIELD: bool(enabled)},
        )
        return {"user_id": target_id, NEW_BOOKING_ALERTS_FIELD: bool(enabled)}

    # ------------------------------------------------------------------
    # Universal message: staff -> one customer, through its template
    # ------------------------------------------------------------------

    async def send_universal_message(
        self, actor_id: str, actor_role: str, actor_center_id: str | None, customer_id: str, message: str,
    ) -> dict:
        """"Hi {name}, {message} — Team Blussit" via the approved
        universal_message template (else the generic update template; in
        production never free text outside the 24-hour window). Admin: any
        customer; manager: customers known to their center. Audited."""
        from app.core.authz import ensure_customer_in_scope
        from app.services.audit_service import AuditService

        if actor_role not in ("admin", "manager"):
            raise ForbiddenException("Only admins and managers can message customers")
        text = "\n".join(" ".join(line.split()) for line in str(message or "").strip().splitlines()).strip()
        if not text:
            raise BadRequestException("Write a message first")
        if len(text) > UNIVERSAL_MESSAGE_MAX:
            raise BadRequestException(f"Keep the message under {UNIVERSAL_MESSAGE_MAX} characters")
        await ensure_customer_in_scope(self.db, actor_role, actor_center_id, customer_id)
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer or customer.get("role") != "customer":
            raise NotFoundException("Customer not found")
        # The queue keeps one row per send (its _id starts with the event and
        # the recipient — an indexed prefix scan).
        recent = await self.queue.count_documents({
            "_id": {"$regex": f"^universal_message:-:{re.escape(customer_id)}:"},
            "created_at": {"$gte": _utcnow() - timedelta(hours=24)},
        })
        if recent >= UNIVERSAL_MESSAGE_DAILY_CAP:
            raise TooManyRequestsException(
                f"This customer already got {UNIVERSAL_MESSAGE_DAILY_CAP} messages from Blussit in the last 24 hours — try again tomorrow."
            )
        first = (str(customer.get("full_name") or "").split() or ["there"])[0]
        title = "Message From Blussit"
        params = [first, text]
        await self.notify(
            customer_id, title, text, NotificationType.SYSTEM, None,
            wa_event="universal_message", wa_params=params, background=False,
        )
        key = _queue_key(customer_id, title, text, None, "universal_message", params, False)
        row = await self.queue.find_one({"_id": key}, {"status": 1, "failure": 1, "channel": 1, "template_name": 1, "last_error": 1}) or {}
        result = {
            "customer_id": customer_id,
            "status": row.get("status") or "not_sent",
            "failure": row.get("failure"),
            "error": row.get("last_error"),
            "channel": row.get("channel"),
            "template_name": row.get("template_name"),
        }
        await AuditService(self.db).log_action(
            actor_id, actor_role, "WHATSAPP_UNIVERSAL_MESSAGE", "users", customer_id,
            {"message": text[:200], "status": result["status"], "failure": result["failure"]},
            service_center_id=actor_center_id if actor_role == "manager" else None,
        )
        return result

    # ------------------------------------------------------------------
    # Sweeps (run by the worker loop — see main.py / docs/ARCHITECTURE.md)
    # ------------------------------------------------------------------

    async def _claim(self, key: str, **fields) -> bool:
        """One-time claim on a sweep item (atomic across instances)."""
        try:
            await self.db[CLAIMS_COLLECTION].insert_one({"_id": key, "created_at": _utcnow(), **fields})
            return True
        except DuplicateKeyError:
            return False

    async def _visit_fields(self, booking: dict, cars: list[dict]) -> tuple[str, str, str, str, str]:
        """services, reference, date, slot, vehicle — the booking templates' fields."""
        try:
            from app.services.booking_service import BookingService

            services, reference, date, slot, vehicle, _code = await BookingService(self.db)._wa_details(booking, cars)
            return services, reference, date, slot, vehicle
        except Exception:  # noqa: BLE001 — best effort: the plain fields
            logger.exception("Could not build WhatsApp fields for booking %s", booking.get("booking_number"))
            from app.utils.slots import format_slot_12h

            ids = [ObjectId(x) for x in booking.get("service_ids") or [] if ObjectId.is_valid(str(x))]
            names = [s.get("name") for s in await self.db.services.find({"_id": {"$in": ids}}, {"name": 1}).to_list(20)] if ids else []
            date = to_ist(booking["scheduled_date"]).strftime("%d %b %Y") if booking.get("scheduled_date") else ""
            return (", ".join(n for n in names if n) or "Car wash", str(booking.get("booking_number") or ""), date,
                    format_slot_12h(booking.get("scheduled_slot")), str(booking.get("vehicle_label") or "Car"))

    async def _visit_cars(self, booking: dict) -> list[dict]:
        gid = booking.get("booking_group_id")
        if not gid:
            return [booking]
        cars = await self.db.bookings.find(
            {"booking_group_id": gid, "is_deleted": {"$ne": True}, "status": {"$ne": "cancelled"}}
        ).sort("group_offset_minutes", 1).to_list(20)
        return cars or [booking]

    @staticmethod
    def _fully_paid(booking: dict) -> bool:
        if booking.get("payment_status") in ("refund_due", "refunded", "failed"):
            return False
        try:
            from app.services.booking_money import amount_due  # MONEY's (2026-10-07); absent before it lands
        except ImportError:
            amount_due = None
        if amount_due is not None:
            try:
                return amount_due(booking) <= 0.009 and (booking.get("payment_status") == "paid" or float(booking.get("total_amount") or 0) <= 0)
            except Exception:  # noqa: BLE001 — fall back to the plain flag
                logger.exception("amount_due failed for booking %s", booking.get("booking_number"))
        return booking.get("payment_status") == "paid" or float(booking.get("total_amount") or 0) <= 0

    async def send_review_requests(self, now: datetime | None = None, limit: int = SWEEP_LIMIT, checkpoint=None) -> dict:
        """Google review ask, once per completed AND fully paid visit, about
        REVIEW_REQUEST_DELAY after completion, 10 AM–7 PM IST only, and only
        when the admin set a Google review URL and its template is approved.
        Template category decides consent: UTILITY (as submitted —
        feedback on one specific booking) goes to everyone; if Meta files
        it MARKETING, opted-out customers are skipped. Idempotent; safe on
        any number of instances (atomic claims). `checkpoint`: the worker
        pass's budget/lease check, awaited before each booking (it raises to
        stop the sweep — deliberately not caught here)."""
        from app.services.whatsapp_crm_service import WhatsAppCrmService

        now = to_ist(now or now_ist())
        if not in_customer_message_hours(now):
            return {"skipped": "outside customer hours"}
        crm = WhatsAppCrmService(self.db)
        url = (await crm.get_settings()).get("google_review_url")
        if not url:
            return {"skipped": "no google_review_url"}
        tpl = await crm.event_template_if_ready("review_request_google")
        if not tpl:
            return {"skipped": "review template not approved"}
        marketing = tpl.get("category") == "MARKETING"
        stats = {"sent": 0, "skipped": 0, "waiting": 0}
        rows = await self.db.bookings.find(
            {
                "status": "completed",
                "closed_at": {"$gte": now - REVIEW_REQUEST_MAX_AGE, "$lte": now - REVIEW_REQUEST_DELAY},
                "review_request_sent_at": {"$exists": False},
                "is_deleted": {"$ne": True},
            }
        ).sort("closed_at", 1).limit(limit).to_list(limit)
        for booking in rows:
            if checkpoint is not None:
                await checkpoint()
            try:
                await self._review_one(booking, now, url, marketing, stats)
            except Exception:  # noqa: BLE001 — one bad row never stops the sweep
                logger.exception("Review request failed for booking %s", booking.get("booking_number"))
        if stats["sent"]:
            logger.info("Review requests: %s", stats)
        return stats

    async def _review_one(self, booking: dict, now: datetime, url: str, marketing: bool, stats: dict) -> None:
        cars = await self._visit_cars(booking)
        if any(c.get("status") != "completed" or not self._fully_paid(c) for c in cars):
            stats["waiting"] += 1  # the visit isn't finished / paid yet
            return
        car_ids = [c["_id"] for c in cars]
        visit = booking.get("booking_group_id") or str(booking["_id"])

        async def stamp(outcome: str) -> None:
            await self.db.bookings.update_many(
                {"_id": {"$in": car_ids}}, {"$set": {"review_request_sent_at": now, "review_request_outcome": outcome}}
            )

        customer_id = str(booking.get("customer_id") or "")
        if not await self._claim(f"review:{visit}", kind="review_request", booking_id=str(booking["_id"]), customer_id=customer_id):
            await stamp("claimed")
            return
        customer = await self.user_repo.find_by_id(customer_id)
        if not customer or customer.get("role") != "customer" or not customer.get("phone"):
            stats["skipped"] += 1
            await stamp("no_recipient")
            return
        if marketing and customer.get("marketing_opt_out"):
            stats["skipped"] += 1
            await stamp("opted_out")
            return
        last = customer.get("last_review_request_at")
        if last and from_stored(last) > now - timedelta(days=REVIEW_REQUEST_COOLDOWN_DAYS):
            stats["skipped"] += 1
            await stamp("recently_asked")
            return
        services, *_ = await self._visit_fields(booking, cars)
        first = (str(customer.get("full_name") or "").split() or ["there"])[0]
        await stamp("sent")
        await self.db.users.update_one({"_id": customer["_id"]}, {"$set": {"last_review_request_at": now}})
        await self.notify(
            customer_id, "How Was Your Wash?",
            f"Thanks for choosing Blussit for your {services}. Rate us on Google: {url}",
            NotificationType.SYSTEM, str(booking["_id"]),
            wa_event="review_request_google", wa_params=[first, services], wa_marketing=marketing, background=False,
        )
        stats["sent"] += 1

    async def list_for_user(self, user_id: str, page: int, page_size: int, unread_only: bool = False):
        items, total = await self.repo.list_for_user(user_id, page, page_size, unread_only)
        return serialize_list(items), total

    async def clear_handled_alerts(self, user_id: str) -> int:
        """Marks read a manager's open alerts whose job is already done, so
        the bell only lists what still needs him:
          - "New booking" / "Still needs a captain": a captain is assigned,
          - "🚨 Urgent": the issue was resolved,
          - any of these: the booking was completed, cancelled or deleted,
          - "New complaint": the complaint was resolved or closed.
        Everything else clears once he opens it. Returns how many cleared."""
        rows = await self.db.notifications.find(
            {
                "user_id": user_id,
                "is_read": False,
                "is_deleted": {"$ne": True},
                "reference_id": {"$nin": [None, ""]},
                "notification_type": {"$in": [NotificationType.BOOKING.value, NotificationType.COMPLAINT.value]},
            },
            {"title": 1, "reference_id": 1, "notification_type": 1},
        ).sort("created_at", -1).limit(CLEAR_SCAN_LIMIT).to_list(CLEAR_SCAN_LIMIT)
        if not rows:
            return 0

        def ref_ids(kind: str) -> list[ObjectId]:
            return list({ObjectId(r["reference_id"]) for r in rows if r.get("notification_type") == kind and ObjectId.is_valid(r["reference_id"])})

        booking_ids = ref_ids(NotificationType.BOOKING.value)
        complaint_ids = ref_ids(NotificationType.COMPLAINT.value)
        # Raw collections on purpose: a deleted booking must count as handled.
        bookings = {
            str(b["_id"]): b
            async for b in self.db.bookings.find({"_id": {"$in": booking_ids}}, {"status": 1, "captain_id": 1, "issue_flag": 1, "is_deleted": 1})
        } if booking_ids else {}
        complaints = {
            str(c["_id"]): c
            async for c in self.db.complaints.find({"_id": {"$in": complaint_ids}}, {"status": 1, "is_deleted": 1})
        } if complaint_ids else {}

        done: list[ObjectId] = []
        for r in rows:
            title = str(r.get("title") or "")
            ref = str(r["reference_id"])
            if r.get("notification_type") == NotificationType.COMPLAINT.value:
                if not title.startswith("New complaint"):
                    continue
                c = complaints.get(ref)
                if c is None or c.get("is_deleted") or c.get("status") in CLOSED_COMPLAINT:
                    done.append(r["_id"])
                continue
            needs_captain = title.startswith(NEEDS_CAPTAIN_TITLES)
            urgent = title.startswith(URGENT_TITLE)
            if not (needs_captain or urgent):
                continue
            b = bookings.get(ref)
            if (
                b is None
                or b.get("is_deleted")
                or b.get("status") in FINISHED_BOOKING
                or (needs_captain and b.get("captain_id"))
                or (urgent and not b.get("issue_flag"))
            ):
                done.append(r["_id"])
        if not done:
            return 0
        result = await self.db.notifications.update_many(
            {"_id": {"$in": done}, "user_id": user_id, "is_read": False},
            {"$set": {"is_read": True, "auto_cleared": True, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count

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
