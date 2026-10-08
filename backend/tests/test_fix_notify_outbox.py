"""
Fix-pass regressions (audit 2026-10-07) for the WhatsApp delivery queue:

  FAIL-02  a failed WhatsApp send is retried later (exponential backoff),
           delivered once, dead-lettered after N attempts, and two retry
           workers never send the same row twice;
  FAIL-01  a request path does not wait out a slow Meta call;
  idempotency  the same notify() call twice enqueues and sends once;
  DEP-04   in production, free text is never sent outside the 24-hour
           window — the queue row fails with a clear reason instead;
  NTF-07   the generic fallback never reads "X booked, X on …".
"""
import asyncio
import itertools
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.enums import NotificationType
from app.services import notification_service, whatsapp_service
from app.services.notification_service import NotificationService
from app.services.whatsapp_service import LogWhatsAppProvider, WhatsAppService

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:6]
_DIGITS = "".join(str(int(c, 16) % 10) for c in _RUN)
_seq = itertools.count(1)


def _phone() -> str:
    return f"62{_DIGITS}{next(_seq):02d}"[:10]


async def _customer(db, cleanup, **extra) -> tuple[str, str]:
    phone = _phone()
    uid = str((await db.users.insert_one({
        "full_name": "Queue Tester", "phone": phone, "role": "customer", "status": "active",
        "is_deleted": False, "created_at": datetime.now(timezone.utc), **extra,
    })).inserted_id)
    for coll, flt in (("users", {"_id": ObjectId(uid)}), ("notifications", {"user_id": uid}),
                      ("whatsapp_outbox", {"phone": phone}), ("whatsapp_queue", {"user_id": uid})):
        cleanup.append((coll, flt))
    return uid, phone


async def _drain():
    for _ in range(100):
        pending = [t for t in notification_service._background_tasks if not t.done()]
        if not pending:
            return
        await asyncio.sleep(0.05)


@pytest.fixture
def meta(monkeypatch):
    """Meta Cloud provider with a fake transport. `install(respond)`;
    respond(to, template_name) -> httpx.Response or raises."""
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PROVIDER", "meta_cloud")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_ACCESS_TOKEN", "x")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "blussit_service_update_fixntf")
    calls: list[tuple[str, str]] = []

    def install(respond):
        class _FakeMeta:
            async def post(self, url, json=None, headers=None, **kw):
                to = (json or {}).get("to", "")
                name = ((json or {}).get("template") or {}).get("name") or (json or {}).get("type")
                calls.append((to, name))
                return await respond(to, name, url)

        monkeypatch.setattr(whatsapp_service, "_meta_http", lambda: _FakeMeta())
        return calls

    return install


def _ok(url):
    return httpx.Response(200, json={"messages": [{"id": f"wamid.FIXQ{uuid.uuid4().hex[:10]}"}]}, request=httpx.Request("POST", url))


async def _queue_row(db, uid):
    return await db.whatsapp_queue.find_one({"user_id": uid})


# ---------------------------------------------------------------- FAIL-02
@pytest.mark.parametrize("error", [httpx.ConnectTimeout("down"), httpx.PoolTimeout("pool exhausted")])
async def test_transport_failure_is_retried_later_and_sent_once(db, cleanup, meta, error):
    uid, phone = await _customer(db, cleanup)
    state = {"down": True}

    async def respond(to, name, url):
        if state["down"]:
            raise error
        return _ok(url)

    calls = meta(respond)
    await NotificationService(db).notify(uid, "Captain assigned", "Ravi is your captain.", NotificationType.BOOKING, str(ObjectId()))
    row = await _queue_row(db, uid)
    assert row is not None, "a failed send must leave a durable queue row"
    assert row["status"] == "pending" and row["attempts"] == 1 and row["failure"] == "transport"
    assert row["next_at"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)  # backoff, not a hot loop
    mine = [c for c in calls if c[0].endswith(phone)]
    assert len(mine) == 1

    # Not due yet: nothing is retried.
    service = NotificationService(db)
    await service.retry_outbox()
    assert len([c for c in calls if c[0].endswith(phone)]) == 1

    # Meta is back and the row is due: delivered exactly once.
    state["down"] = False
    await db.whatsapp_queue.update_one({"_id": row["_id"]}, {"$set": {"next_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    stats = await service.retry_outbox()
    assert stats["sent"] >= 1
    row = await _queue_row(db, uid)
    assert row["status"] == "sent" and row["attempts"] == 2 and row.get("sent_at")
    # Idempotent: another run sends nothing more.
    await service.retry_outbox()
    assert len([c for c in calls if c[0].endswith(phone)]) == 2


async def test_gives_up_after_max_attempts_into_dead_letter(db, cleanup, meta):
    uid, phone = await _customer(db, cleanup)

    async def respond(to, name, url):
        raise httpx.ConnectError("down")

    meta(respond)
    await NotificationService(db).notify(uid, "Booking cancelled", "BK-1 was cancelled.", NotificationType.BOOKING, str(ObjectId()))
    row = await _queue_row(db, uid)
    await db.whatsapp_queue.update_one(
        {"_id": row["_id"]},
        {"$set": {"attempts": notification_service.QUEUE_MAX_ATTEMPTS - 1, "next_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    await NotificationService(db).retry_outbox()
    row = await _queue_row(db, uid)
    assert row["status"] == "dead" and row["failure"] == "transport" and row["attempts"] == notification_service.QUEUE_MAX_ATTEMPTS


async def test_two_retry_workers_never_send_the_same_row_twice(db, cleanup, meta):
    uid, phone = await _customer(db, cleanup)
    state = {"down": True}

    async def respond(to, name, url):
        if state["down"]:
            raise httpx.ReadTimeout("down")
        await asyncio.sleep(0.2)  # slow enough for the two workers to overlap
        return _ok(url)

    calls = meta(respond)
    await NotificationService(db).notify(uid, "Service started", "Your captain has started.", NotificationType.BOOKING, str(ObjectId()))
    row = await _queue_row(db, uid)
    state["down"] = False
    await db.whatsapp_queue.update_one({"_id": row["_id"]}, {"$set": {"next_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    await asyncio.gather(NotificationService(db).retry_outbox(), NotificationService(db).retry_outbox())
    assert len([c for c in calls if c[0].endswith(phone)]) == 2  # the first failed try + ONE retry
    assert (await _queue_row(db, uid))["status"] == "sent"


async def test_a_row_orphaned_mid_send_is_recovered_without_a_second_send(db, cleanup):
    """A process died while a send was in flight (lease expired). If the
    provider had already recorded the send, the retry marks it sent instead
    of messaging the customer again; otherwise it sends it."""
    uid, phone = await _customer(db, cleanup)
    service = NotificationService(db)
    await service.notify(uid, "Captain on the way", "Your captain has left.", NotificationType.BOOKING, str(ObjectId()))
    row = await _queue_row(db, uid)
    assert row["status"] == "sent"
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1

    expired = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db.whatsapp_queue.update_one({"_id": row["_id"]}, {"$set": {"status": "sending", "lease_until": expired}})
    await service.retry_outbox()
    assert (await _queue_row(db, uid))["status"] == "sent"
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1, "already delivered: never sent twice"

    # Orphaned BEFORE the provider ever sent it: the retry delivers it.
    uid2, phone2 = await _customer(db, cleanup)
    await db.whatsapp_queue.insert_one({
        "_id": f"orphan:{uid2}", "user_id": uid2, "phone": phone2, "title": "Service started", "message": "Started.",
        "notification_type": "booking", "reference_id": None, "wa_event": None, "wa_params": None, "wa_marketing": False,
        "status": "sending", "lease_until": expired, "lease_owner": "dead-instance", "attempts": 0,
        "next_at": expired, "created_at": expired, "updated_at": expired,
    })
    await service.retry_outbox()
    assert (await _queue_row(db, uid2))["status"] == "sent"
    assert await db.whatsapp_outbox.count_documents({"phone": phone2}) == 1


# ------------------------------------------------------------ idempotency
async def test_duplicate_notify_calls_enqueue_and_send_once(db, cleanup):
    uid, phone = await _customer(db, cleanup)
    ref = str(ObjectId())
    service = NotificationService(db)
    args = (uid, "Captain assigned", "Ravi is your captain.", NotificationType.BOOKING, ref)
    await asyncio.gather(service.notify(*args, wa_event="captain_assigned", wa_params=["Ravi"]),
                         service.notify(*args, wa_event="captain_assigned", wa_params=["Ravi"]))
    await service.notify(*args, wa_event="captain_assigned", wa_params=["Ravi"])
    assert await db.whatsapp_queue.count_documents({"user_id": uid}) == 1
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1

    # Different content (a reassignment to another captain) is a new message.
    await service.notify(uid, "Captain assigned", "Amit is your captain.", NotificationType.BOOKING, ref,
                         wa_event="captain_assigned", wa_params=["Amit"])
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 2


# ---------------------------------------------------------------- FAIL-01
async def test_request_path_does_not_wait_out_a_slow_send(db, cleanup, monkeypatch):
    uid, phone = await _customer(db, cleanup)
    real_send = LogWhatsAppProvider.send

    async def slow_send(self, *a, **k):
        await asyncio.sleep(3)
        return await real_send(self, *a, **k)

    monkeypatch.setattr(LogWhatsAppProvider, "send", slow_send)
    t = time.monotonic()
    await NotificationService(db).notify(uid, "Booking rescheduled", "Moved to 9 AM.", NotificationType.BOOKING, str(ObjectId()))
    took = time.monotonic() - t
    assert took < notification_service.INLINE_SEND_WAIT_SECONDS + 1.0, took
    assert await db.notifications.count_documents({"user_id": uid}) == 1  # in-app row written before returning
    await _drain()
    assert (await _queue_row(db, uid))["status"] == "sent"
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1


async def test_bot_style_background_false_still_waits_in_order(db, cleanup, monkeypatch):
    uid, phone = await _customer(db, cleanup)
    real_send = LogWhatsAppProvider.send

    async def slow_send(self, *a, **k):
        await asyncio.sleep(1.5)
        return await real_send(self, *a, **k)

    monkeypatch.setattr(LogWhatsAppProvider, "send", slow_send)
    monkeypatch.setattr(notification_service, "INLINE_SEND_WAIT_SECONDS", 0.2)
    await NotificationService(db).notify(uid, "Booked", "Done.", background=False)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1


# ---------------------------------------------------------------- DEP-04
@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "")
    monkeypatch.setattr(settings, "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME", "")


async def test_production_never_sends_free_text_outside_the_window(db, cleanup, production):
    uid, phone = await _customer(db, cleanup)
    await NotificationService(db).notify(uid, "Complaint update", "Your complaint has an update.", NotificationType.COMPLAINT, str(ObjectId()))
    row = await _queue_row(db, uid)
    assert row["status"] == "failed" and row["failure"] == "no_template", row
    out = await db.whatsapp_outbox.find({"phone": phone}).to_list(None)
    assert len(out) == 1 and out[0]["ok"] is False and out[0]["failure"] == "no_template"  # visible, never "sent"
    health = await NotificationService(db).delivery_health(days=1)
    assert health["queue"]["failed"] >= 1 and health["failures_by_reason"].get("no_template", 0) >= 1


async def test_production_free_text_goes_out_inside_the_window(db, cleanup, production):
    uid, phone = await _customer(db, cleanup)
    cleanup.append(("whatsapp_conversations", {"wa_id": f"91{phone}"}))
    await db.whatsapp_conversations.insert_one({"wa_id": f"91{phone}", "phone": phone, "last_inbound_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    await NotificationService(db).notify(uid, "Complaint update", "Your complaint has an update.", NotificationType.COMPLAINT, str(ObjectId()))
    assert (await _queue_row(db, uid))["status"] == "sent"
    out = await db.whatsapp_outbox.find_one({"phone": phone})
    assert out.get("ok") is not False and "template_name" not in out


async def test_production_rejected_update_template_counts_as_missing(db, cleanup, production, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "blussit_update_fixntf_rejected")
    await db.whatsapp_templates.update_one({"name": "blussit_update_fixntf_rejected"}, {"$set": {"status": "REJECTED"}}, upsert=True)
    cleanup.append(("whatsapp_templates", {"name": "blussit_update_fixntf_rejected"}))
    uid, phone = await _customer(db, cleanup)
    await NotificationService(db).notify(uid, "Complaint update", "Your complaint has an update.")
    row = await _queue_row(db, uid)
    assert row["status"] == "failed" and row["failure"] == "no_template"


async def test_production_temp_password_without_template_reports_not_sent(db, cleanup, production):
    phone = _phone()
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    wa = WhatsAppService(db)
    outcome = await wa.send_temp_password_outcome(phone, "Tp99XyZz")
    assert outcome.ok is False and outcome.reason == "no_template"
    assert await wa.send_temp_password(phone, "Tp99XyZz") is False
    assert await wa.can_deliver_temp_password(phone) is False
    rows = await db.whatsapp_outbox.find({"phone": phone}).to_list(None)
    assert rows and all(r["ok"] is False for r in rows)
    assert all("Tp99XyZz" not in r["message"] for r in rows)


# ---------------------------------------------------------------- NTF-07
def test_generic_composition_never_repeats_the_subject():
    from app.services.whatsapp_service import compose_update

    head, body = compose_update(
        "Star Wash booked", "Star Wash on 08 Oct at 11:00 AM – 2:00 PM is confirmed. (BK-1)", NotificationType.BOOKING
    )
    assert f"{head} {body}".count("Star Wash") == 1
    assert "11:00 AM" in body and "BK-1" in body
    # A body that repeats the whole title keeps the title once.
    head, body = compose_update("Payment received", "Payment received — ₹300 for BK-2.")
    assert head == "Payment received" and body.startswith("₹300")
    # Ordinary pairs are untouched.
    assert compose_update("Captain assigned", "A captain has been assigned to your booking.") == (
        "Captain assigned", "A captain has been assigned to your booking.")


async def test_booking_confirmation_fallback_reads_once(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "blussit_service_update_v3")
    uid, phone = await _customer(db, cleanup)
    await NotificationService(db).notify(
        uid, "Star Wash booked", "Star Wash on 08 Oct at 11:00 AM – 2:00 PM is confirmed. (BK-FIX1)", NotificationType.BOOKING,
        str(ObjectId()), wa_event="booking_confirmed_fixntf_unknown", wa_params=["Star Wash", "BK-FIX1"],
    )
    out = await db.whatsapp_outbox.find_one({"phone": phone})
    assert out["template_name"] == "blussit_service_update_v3"
    assert out["message"].count("Star Wash") == 1, out["message"]
