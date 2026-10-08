"""
Fix-pass regressions (audit 2026-10-07), WhatsApp bot / webhook side:

  AUTH-05  a staff member's own phone messaging the business number never
           starts the customer flow or touches their account — the chat is
           left to the CRM inbox;
  PAY-06   "Cash after wash" voids the open payment link it replaces;
  NTF-05   "STOP" / Meta's 131050 / the user_preferences webhook record the
           marketing opt-out; marketing is skipped afterwards, utility isn't;
  DEP-04   bot replies still go out in production (the chat is open), a
           proactive interactive nudge outside the window is not sent;
           template status webhooks update the local template; the hourly
           template sync runs once per period.
"""
import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.enums import NotificationType
from app.services import payment_service
from app.services.notification_service import NotificationService
from app.services.whatsapp_bot_service import WhatsAppBotService
from app.services.whatsapp_crm_service import WhatsAppCrmService

from tests.factories import make_captain, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:6]
_DIGITS = "".join(str(int(c, 16) % 10) for c in _RUN)
_seq = itertools.count(1)


def _payload(wa_id, *, text=None, reply=None):
    if text is not None:
        msg = {"type": "text", "text": {"body": text}}
    else:
        msg = {"type": "interactive", "interactive": {"button_reply": {"id": reply, "title": "x"}}}
    msg["from"] = wa_id
    msg["id"] = f"wamid.FIXBOT{_RUN}{next(_seq):08d}"
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": wa_id, "profile": {"name": "Fix Bot"}}], "messages": [msg]}}]}]}


def _track_wa(cleanup, phone):
    cleanup.append(("whatsapp_conversations", {"wa_id": f"91{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_inbox", {"wa_id": f"91{phone}"}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": f"^wamid.FIXBOT{_RUN}"}}))


async def _customer(db, cleanup, **extra) -> tuple[str, str]:
    phone = f"63{_DIGITS}{next(_seq):02d}"[:10]
    uid = str((await db.users.insert_one({
        "full_name": "Bot Fix Customer", "phone": phone, "role": "customer", "status": "active", "is_deleted": False,
        "phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "created_at": datetime.now(timezone.utc), **extra,
    })).inserted_id)
    for coll, flt in (("users", {"_id": ObjectId(uid)}), ("notifications", {"user_id": uid}), ("whatsapp_queue", {"user_id": uid}),
                      ("bookings", {"customer_id": uid}), ("payment_orders", {"customer_id": uid})):
        cleanup.append((coll, flt))
    _track_wa(cleanup, phone)
    return uid, phone


# ---------------------------------------------------------------- AUTH-05
@pytest.mark.parametrize("role", ["manager", "captain"])
async def test_staff_phone_never_enters_the_customer_flow(db, cleanup, role):
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    staff_id = await (make_manager(db, center) if role == "manager" else make_captain(db, center))
    cleanup.append(("users", {"_id": ObjectId(staff_id)}))
    cleanup.append(("captain_wallets", {"captain_id": staff_id}))
    before = await db.users.find_one({"_id": ObjectId(staff_id)})
    phone = before["phone"]
    wa_id = f"91{phone}"
    _track_wa(cleanup, phone)

    bot = WhatsAppBotService(db)
    await bot.handle_webhook(_payload(wa_id, text="hi"))
    await bot.handle_webhook(_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(_payload(wa_id, text="hello?"))

    after = await db.users.find_one({"_id": ObjectId(staff_id)})
    assert after.get("password_hash") == before.get("password_hash"), "staff password must survive"
    assert after.get("token_version") == before.get("token_version")
    assert after.get("phone_verified") == before.get("phone_verified")
    assert await db.users.count_documents({"phone": phone}) == 1  # no customer twin either
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert not convo.get("customer_id") and convo.get("state") in (None, "staff")
    assert await db.whatsapp_inbox.count_documents({"wa_id": wa_id}) == 3  # the inbox still has every message
    replies = await db.whatsapp_outbox.find({"phone": phone}).to_list(None)
    assert len(replies) == 1 and "staff" in replies[0]["message"].lower()  # told once, not on every line


async def test_a_conversation_wrongly_linked_to_staff_is_unlinked(db, cleanup):
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    manager_id = await make_manager(db, center)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    phone = (await db.users.find_one({"_id": ObjectId(manager_id)}))["phone"]
    _track_wa(cleanup, phone)
    await db.whatsapp_conversations.insert_one({"wa_id": f"91{phone}", "customer_id": manager_id, "state": "menu", "data": {}})
    await WhatsAppBotService(db).handle_webhook(_payload(f"91{phone}", reply="menu:book"))
    convo = await db.whatsapp_conversations.find_one({"wa_id": f"91{phone}"})
    assert "customer_id" not in convo
    assert (await db.users.find_one({"_id": ObjectId(manager_id)})).get("password_hash")


# ---------------------------------------------------------------- PAY-06
class _Links:
    def __init__(self):
        self.cancelled: list[str] = []

    def create(self, payload):
        n = next(_seq)
        return {"id": f"plink_fixbot_{_RUN}_{n}", "short_url": f"https://rzp.io/l/fixbot{_RUN}{n}", **payload}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


async def test_cash_after_online_voids_the_open_link(db, cleanup, monkeypatch):
    links = _Links()

    class _Client:
        payment_link = links

    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())
    uid, phone = await _customer(db, cleanup)
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    await db.whatsapp_conversations.insert_one({"wa_id": f"91{phone}", "customer_id": uid, "state": None, "data": {}})
    res = await db.bookings.insert_one({
        "booking_number": f"BK-FIXCASH{_RUN}", "customer_id": uid, "customer_phone": phone, "service_center_id": center,
        "status": "pending", "payment_method": "cash", "payment_status": "pending", "total_amount": 349.0,
        "scheduled_date": datetime.now(timezone.utc).replace(tzinfo=None), "scheduled_slot": "09:00-12:00", "is_deleted": False,
    })
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(_payload(f"91{phone}", reply=f"pay:online:{res.inserted_id}"))
    link = await db.payment_orders.find_one({"kind": "link", "booking_id": str(res.inserted_id)})
    assert link and link["status"] == "created"

    await bot.handle_webhook(_payload(f"91{phone}", reply=f"pay:cash:{res.inserted_id}"))
    assert (await db.bookings.find_one({"_id": res.inserted_id}))["payment_method"] == "cash"
    link = await db.payment_orders.find_one({"_id": link["_id"]})
    assert link["status"] == "voided", "the customer must not be able to pay the old link too"
    assert links.cancelled == [link["razorpay_link_id"]]


# ---------------------------------------------------------------- NTF-05
async def _approve(db, cleanup, name, category):
    await db.whatsapp_templates.update_one(
        {"name": name}, {"$set": {"name": name, "status": "APPROVED", "category": category, "disabled": False, "fixbot": True}}, upsert=True)
    cleanup.append(("whatsapp_templates", {"name": name, "fixbot": True}))


async def test_stop_opts_out_of_marketing_but_not_updates(db, cleanup):
    await _approve(db, cleanup, "blussit_repeat_booking", "MARKETING")
    uid, phone = await _customer(db, cleanup)
    await WhatsAppBotService(db).handle_webhook(_payload(f"91{phone}", text="STOP"))
    user = await db.users.find_one({"_id": ObjectId(uid)})
    assert user["marketing_opt_out"] is True
    assert "offers" in (await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)]))["message"].lower()

    before = await db.whatsapp_outbox.count_documents({"phone": phone})
    notes = NotificationService(db)
    await notes.notify(uid, "Time for a wash?", "Book again.", NotificationType.SYSTEM, None,
                       wa_event="repeat_booking", wa_params=["Bot"], wa_marketing=True)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == before, "marketing skipped after STOP"
    await notes.notify(uid, "Captain assigned", "A captain has been assigned to your booking.", NotificationType.BOOKING, str(ObjectId()))
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == before + 1, "utility updates continue"

    # "subscribe" turns offers back on.
    await WhatsAppBotService(db).handle_webhook(_payload(f"91{phone}", text="subscribe"))
    assert (await db.users.find_one({"_id": ObjectId(uid)}))["marketing_opt_out"] is False


async def test_meta_131050_and_user_preferences_record_the_opt_out(db, cleanup):
    uid, phone = await _customer(db, cleanup)
    wamid = f"wamid.FIXBOT131050{_RUN}"
    await db.whatsapp_outbox.insert_one({"phone": phone, "message": "promo", "ok": True, "wamid": wamid, "template_name": "blussit_we_miss_you"})
    bot = WhatsAppBotService(db)
    await bot.handle_webhook({"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{
        "id": wamid, "status": "failed", "recipient_id": f"91{phone}",
        "errors": [{"code": 131050, "title": "Unable to deliver the message. This recipient has chosen to stop receiving marketing messages"}],
    }]}}]}]})
    assert (await db.users.find_one({"_id": ObjectId(uid)}))["marketing_opt_out"] is True

    await bot.handle_webhook({"entry": [{"changes": [{"field": "user_preferences", "value": {"user_preferences": [
        {"wa_id": f"91{phone}", "category": "marketing_messages", "value": "resume", "detail": "User resumed"}]}}]}]})
    assert (await db.users.find_one({"_id": ObjectId(uid)}))["marketing_opt_out"] is False
    await bot.handle_webhook({"entry": [{"changes": [{"field": "user_preferences", "value": {"user_preferences": [
        {"wa_id": f"91{phone}", "category": "marketing_messages", "value": "stop", "detail": "User requested to stop"}]}}]}]})
    assert (await db.users.find_one({"_id": ObjectId(uid)}))["marketing_opt_out"] is True


# ---------------------------------------------------------------- DEP-04
async def test_bot_replies_in_production_but_proactive_nudge_waits_for_the_window(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    uid, phone = await _customer(db, cleanup)
    await WhatsAppBotService(db).handle_webhook(_payload(f"91{phone}", text="hi"))
    menu = await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])
    assert menu.get("ok") is not False and menu.get("interactive_kind") == "buttons"

    # The chat closed long ago: the proactive pay nudge must not go out as
    # free text (Meta would accept and silently drop it).
    await db.whatsapp_conversations.update_one(
        {"wa_id": f"91{phone}"}, {"$set": {"last_inbound_at": datetime.now(timezone.utc) - timedelta(hours=30)}})
    booking = {"_id": ObjectId(), "booking_number": "BK-FIXNUDGE", "customer_phone": phone, "total_amount": 299.0}
    before = await db.whatsapp_outbox.count_documents({"phone": phone, "ok": {"$ne": False}})
    assert await WhatsAppBotService(db).send_payment_reminder(booking) is False
    assert await db.whatsapp_outbox.count_documents({"phone": phone, "ok": {"$ne": False}}) == before


async def test_template_status_webhook_updates_the_local_template(db, cleanup):
    name = f"blussit_fixbot_tpl_{_RUN}"
    await db.whatsapp_templates.insert_one({"name": name, "status": "APPROVED", "category": "UTILITY", "param_count": 1, "disabled": False})
    cleanup.append(("whatsapp_templates", {"name": name}))
    await WhatsAppBotService(db).handle_webhook({"entry": [{"changes": [{"field": "message_template_status_update", "value": {
        "event": "PAUSED", "message_template_id": 1, "message_template_name": name, "message_template_language": "en_US", "reason": "LOW_QUALITY"}}]}]})
    tpl = await db.whatsapp_templates.find_one({"name": name})
    assert tpl["status"] == "PAUSED" and tpl["rejected_reason"] == "LOW_QUALITY"
    await WhatsAppBotService(db).handle_webhook({"entry": [{"changes": [{"field": "template_category_update", "value": {
        "message_template_id": 1, "message_template_name": name, "message_template_language": "en_US",
        "previous_category": "UTILITY", "new_category": "MARKETING"}}]}]})
    assert (await db.whatsapp_templates.find_one({"name": name}))["category"] == "MARKETING"


async def test_template_sync_runs_once_per_period(db, cleanup, monkeypatch):
    cleanup.append(("locks", {"_id": "whatsapp_template_sync"}))
    await db.locks.delete_many({"_id": "whatsapp_template_sync"})
    calls = []

    async def fake_sync(self):
        calls.append(1)
        return {"synced": 3}

    monkeypatch.setattr(WhatsAppCrmService, "sync_templates", fake_sync)
    crm = WhatsAppCrmService(db)
    assert (await crm.sync_templates_if_due(max_age_minutes=60))["synced"] == 3
    assert (await crm.sync_templates_if_due(max_age_minutes=60)).get("skipped") is True
    assert len(calls) == 1
    await db.locks.update_one({"_id": "whatsapp_template_sync"}, {"$set": {"synced_at": datetime.now(timezone.utc) - timedelta(minutes=61)}})
    assert (await crm.sync_templates_if_due(max_age_minutes=60))["synced"] == 3
    assert len(calls) == 2
