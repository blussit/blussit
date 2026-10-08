"""
Regressions for the 2026-10 notification / WhatsApp audit:

  NTF-4  a bot handoff expires after a quiet period (an agent takeover never
         does), and stateless pay buttons are honoured even while paused;
  EXT-3  "Pay online" reuses a still-open link; Cash after Online reverts
         the method; a car already paid in cash is never relabelled online;
  NTF-5  a deleted account's number can book on WhatsApp again;
  NTF-7  a double-tapped Confirm answers "already booked";
  NTF-10 a bot booking gets ONE WhatsApp confirmation;
  NTF-15 admins are pinged once per conversation, bot-collected names are
         capped and cleaned;
  EXT-4  a Meta outage costs one blocking attempt, not two;
  NTF-8  OTPs / temp passwords are never stored or logged in clear.
"""
import asyncio
import itertools
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.enums import NotificationType
from app.schemas.booking_schema import QuickBookingLine, QuickBookingRequest
from app.services import payment_service, sms_service, whatsapp_service
from app.services.booking_service import BookingService
from app.services.notification_service import NotificationService
from app.services.user_service import UserService
from app.services.whatsapp_bot_service import ADMIN_PING_COOLDOWN_HOURS, WhatsAppBotService
from app.services.whatsapp_crm_service import BOT_HANDOFF_RESUME_HOURS, WhatsAppCrmService
from app.services.whatsapp_service import WhatsAppService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:6]
_DIGITS = "".join(str(int(c, 16) % 10) for c in _RUN)
_seq = itertools.count(1)
HELP_TITLE = "WhatsApp customer needs help"


def _wa(prefix: str) -> tuple[str, str]:
    """A per-run unique WhatsApp id and its 10-digit local phone."""
    phone = (prefix + _DIGITS)[:10]
    return f"91{phone}", phone


def wa_payload(wa_id, *, text=None, reply=None, location=None, name="WA Fixes"):
    if text is not None:
        msg = {"type": "text", "text": {"body": text}}
    elif reply is not None:
        msg = {"type": "interactive", "interactive": {"button_reply": {"id": reply, "title": "x"}}}
    else:
        msg = {"type": "location", "location": {"latitude": location[0], "longitude": location[1]}}
    msg["from"] = wa_id
    msg["id"] = f"wamid.FIXNTF{_RUN}{next(_seq):08d}"
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": wa_id, "profile": {"name": name}}], "messages": [msg]}}]}]}


class _Links:
    def create(self, payload):
        n = next(_seq)
        return {"id": f"plink_fixntf_{_RUN}_{n}", "short_url": f"https://rzp.io/l/fixntf{_RUN}{n}", **payload}

    def cancel(self, link_id):
        return {"id": link_id, "status": "cancelled"}


class _Client:
    payment_link = _Links()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _Client())


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    default = await db.service_centers.find_one({"is_active": True, "is_deleted": {"$ne": True}}, sort=[("_id", 1)])
    # On the pin drive_to_confirm drops, so a center another suite left at
    # the factory default nearby can't win the nearest-center tie.
    center_id = await make_service_center(
        db,
        working_hours_start=default.get("working_hours_start", "08:00"),
        working_hours_end=default.get("working_hours_end", "20:00"),
        slot_duration_minutes=default.get("slot_duration_minutes") or 180,
        default_slot_capacity=5,
        latitude=22.701,
        longitude=75.801,
    )
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("notifications", {"user_id": manager_id}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": "^wamid.FIXNTF"}}))
    from tests.test_whatsapp_bot import park_competing_centers, unpark_centers

    parked = await park_competing_centers(db, center_id)
    yield {"center_id": center_id, "manager_id": manager_id, "hatchback": hatchback, "foam": foam}
    await unpark_centers(db, parked)


def _wa_cleanup(cleanup, wa_id, phone):
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_inbox", {"wa_id": wa_id}))
    cleanup.append(("notifications", {"title": HELP_TITLE, "message": {"$regex": f"^\\+{wa_id}:"}}))


def _track(cleanup, cid: str):
    for coll, flt in (("addresses", {"owner_id": cid}), ("bookings", {"customer_id": cid}),
                      ("notifications", {"user_id": cid}), ("payment_orders", {"customer_id": cid}),
                      ("users", {"_id": ObjectId(cid)})):
        cleanup.append((coll, flt))


async def drive_to_confirm(db, bot, rig, wa_id, *, new_name=None):
    """hi -> (name) -> book -> type -> service -> when -> address -> Confirm buttons."""
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    if new_name:
        await bot.handle_webhook(wa_payload(wa_id, text=new_name))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{rig['hatchback']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"svc:{rig['foam']}"))
    date_str = (now_ist().date() + timedelta(days=1)).isoformat()
    slots = await BookingService(db).available_slots(rig["center_id"], date_str)
    slot_key = next(s["key"] for s in slots if s["status"] != "full")
    await bot.handle_webhook(wa_payload(wa_id, reply=f"when:{date_str}|{slot_key}"))
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    if convo.get("state") == "q_address":
        addr = await db.addresses.find_one({"owner_id": convo["customer_id"]})
        await bot.handle_webhook(wa_payload(wa_id, reply=f"addr:{addr['_id']}"))
    else:
        await bot.handle_webhook(wa_payload(wa_id, location=(22.701, 75.801)))
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["state"] == "confirm", convo.get("state")
    return convo


async def book_via_bot(db, bot, rig, wa_id, phone, cleanup, name):
    await drive_to_confirm(db, bot, rig, wa_id, new_name=name)
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    user = await db.users.find_one({"phone": phone})
    cid = str(user["_id"])
    _track(cleanup, cid)
    booking = await db.bookings.find_one({"customer_id": cid})
    assert booking is not None
    return cid, booking


async def _outbox_count(db, phone):
    return await db.whatsapp_outbox.count_documents({"phone": phone})


async def _last_out(db, phone):
    return await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])


async def _two_strikes(bot, wa_id):
    """Out of the bot's depth twice at the vehicle-type step -> handoff."""
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, text="when will captain come"))
    await bot.handle_webhook(wa_payload(wa_id, text="hello??"))


# ---------------------------------------------------------------------------
# NTF-4: handoff expiry + pay buttons while paused
# ---------------------------------------------------------------------------
async def test_pay_button_is_honoured_while_bot_is_handed_off(db, cleanup, rig, gateway):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77701")
    _wa_cleanup(cleanup, wa_id, phone)
    _cid, booking = await book_via_bot(db, bot, rig, wa_id, phone, cleanup, "Handoff Pay")
    await _two_strikes(bot, wa_id)
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is True and convo["bot_paused_by"] == "bot"

    before = await _outbox_count(db, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    assert await _outbox_count(db, phone) == before  # a human has it: chat text is left to them

    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking['_id']}"))
    assert await db.payment_orders.count_documents({"kind": "link", "booking_id": str(booking["_id"])}) == 1
    assert "rzp.io" in (await _last_out(db, phone))["message"]
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is True  # paying doesn't take the thread back from the human


async def test_bot_handoff_resumes_after_quiet_period(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77702")
    _wa_cleanup(cleanup, wa_id, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Quiet Resume"))
    _track(cleanup, str((await db.users.find_one({"phone": phone}))["_id"]))
    await _two_strikes(bot, wa_id)
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["bot_paused"] is True

    # Inside the quiet period: still the human's.
    before = await _outbox_count(db, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    assert await _outbox_count(db, phone) == before

    # Nobody picked it up for longer than the quiet period: the bot is back.
    old = datetime.now(timezone.utc) - timedelta(hours=BOT_HANDOFF_RESUME_HOURS, minutes=5)
    await db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"bot_paused_at": old}})
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    out = await _last_out(db, phone)
    assert out["interactive_kind"] == "buttons" and "What would you like to do?" in out["message"]
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is False and "bot_paused_by" not in convo


async def test_agent_takeover_never_auto_resumes(db, cleanup, rig, gateway):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77703")
    _wa_cleanup(cleanup, wa_id, phone)
    _cid, booking = await book_via_bot(db, bot, rig, wa_id, phone, cleanup, "Agent Owned")
    crm = WhatsAppCrmService(db)
    await crm.send_text(wa_id, "Hi, this is Priya from Blussit.", "000000000000000000000001")
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is True and convo["bot_paused_by"] == "agent"

    # Even days later, the bot stays out of an agent's thread...
    old = datetime.now(timezone.utc) - timedelta(days=3)
    await db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"bot_paused_at": old, "updated_at": old}})
    before = await _outbox_count(db, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    assert await _outbox_count(db, phone) == before
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["bot_paused"] is True

    # ...but a pay button still works there,
    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking['_id']}"))
    assert "rzp.io" in (await _last_out(db, phone))["message"]

    # and resolving hands it back cleanly.
    await crm.set_status(wa_id, "resolved")
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is False and "bot_paused_by" not in convo


# ---------------------------------------------------------------------------
# EXT-3: pay-link reuse, cash <-> online relabelling
# ---------------------------------------------------------------------------
async def test_repeated_pay_online_taps_reuse_one_open_link(db, cleanup, rig, gateway):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77704")
    _wa_cleanup(cleanup, wa_id, phone)
    _cid, booking = await book_via_bot(db, bot, rig, wa_id, phone, cleanup, "Link Reuse")
    urls = []
    for _ in range(3):
        await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking['_id']}"))
        urls.append(next(w for w in (await _last_out(db, phone))["message"].split() if "rzp.io" in w))
    assert await db.payment_orders.count_documents({"kind": "link", "booking_id": str(booking["_id"]), "status": "created"}) == 1
    assert len(set(urls)) == 1

    # A different amount (price changed) is a different link.
    await db.bookings.update_one({"_id": booking["_id"]}, {"$inc": {"total_amount": 10}})
    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking['_id']}"))
    assert await db.payment_orders.count_documents({"kind": "link", "booking_id": str(booking["_id"])}) == 2


async def test_cash_after_online_reverts_the_payment_method(db, cleanup, rig, gateway):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77705")
    _wa_cleanup(cleanup, wa_id, phone)
    _cid, booking = await book_via_bot(db, bot, rig, wa_id, phone, cleanup, "Cash Revert")
    assert booking["payment_method"] == "cash"
    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking['_id']}"))
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["payment_method"] == "online"
    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:cash:{booking['_id']}"))
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["payment_method"] == "cash"
    assert "cash" in (await _last_out(db, phone))["message"]


async def test_pay_online_never_relabels_a_car_already_paid_in_cash(db, cleanup, rig, gateway):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77706")
    _wa_cleanup(cleanup, wa_id, phone)
    convo = await drive_to_confirm(db, bot, rig, wa_id, new_name="Two Cars")
    customer = await db.users.find_one({"phone": phone})
    _track(cleanup, str(customer["_id"]))
    data = convo["data"]
    result = await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Two Cars", customer_phone=phone, address_id=data["address_id"],
            lines=[QuickBookingLine(vehicle_type=rig["hatchback"], quantity=2, service_ids=[rig["foam"]])],
            scheduled_date=data["date"], scheduled_slot=data["slot"],
        ),
        customer=customer, source="whatsapp", allow_pinless=True, notify_background=False,
    )
    paid_id, unpaid_id = (ObjectId(b["id"]) for b in result["bookings"])
    await db.bookings.update_one({"_id": paid_id}, {"$set": {"payment_status": "paid", "payment_method": "cash"}})

    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{unpaid_id}"))
    paid = await db.bookings.find_one({"_id": paid_id})
    unpaid = await db.bookings.find_one({"_id": unpaid_id})
    assert paid["payment_method"] == "cash"
    assert unpaid["payment_method"] == "online"
    link = await db.payment_orders.find_one({"kind": "link", "booking_id": str(unpaid_id)})
    assert link["amount_paise"] == int(round(float(unpaid["total_amount"]) * 100))


# ---------------------------------------------------------------------------
# NTF-5: a deleted account's number can book again
# ---------------------------------------------------------------------------
async def test_delete_user_unlinks_the_whatsapp_conversation(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77707")
    _wa_cleanup(cleanup, wa_id, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Gone Soon"))
    uid = str((await db.users.find_one({"phone": phone}))["_id"])
    _track(cleanup, uid)
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["customer_id"] == uid
    assert await UserService(db).delete_user(uid)
    assert "customer_id" not in await db.whatsapp_conversations.find_one({"wa_id": wa_id})


async def test_dead_customer_link_is_re_resolved_and_can_book(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77708")
    _wa_cleanup(cleanup, wa_id, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Old Account"))
    old_id = str((await db.users.find_one({"phone": phone}))["_id"])
    _track(cleanup, old_id)
    # A delete from before the unlink existed: the conversation still holds the dead id.
    await db.users.delete_one({"_id": ObjectId(old_id)})

    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    assert "name" in (await _last_out(db, phone))["message"]
    assert "customer_id" not in await db.whatsapp_conversations.find_one({"wa_id": wa_id})

    await drive_to_confirm(db, bot, rig, wa_id, new_name="New Account")
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    new_user = await db.users.find_one({"phone": phone})
    new_id = str(new_user["_id"])
    _track(cleanup, new_id)
    assert new_id != old_id and new_user["full_name"] == "New Account"
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["customer_id"] == new_id
    assert await db.bookings.count_documents({"customer_id": new_id}) == 1


# ---------------------------------------------------------------------------
# NTF-7 / NTF-10: one booking, one confirmation
# ---------------------------------------------------------------------------
async def test_double_tapped_confirm_answers_already_booked(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77709")
    _wa_cleanup(cleanup, wa_id, phone)
    convo = await drive_to_confirm(db, bot, rig, wa_id, new_name="Double Tap")
    cid = convo["customer_id"]
    _track(cleanup, cid)
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    booking = await db.bookings.find_one({"customer_id": cid})
    # The second tap's handler loaded the chat before the first one finished.
    await bot._set_state(wa_id, "confirm", convo["data"])
    before = await _outbox_count(db, phone)
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))

    assert await db.bookings.count_documents({"customer_id": cid}) == 1
    replies = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", 1).skip(before).to_list(None)
    assert len(replies) == 1
    assert "already booked" in replies[0]["message"] and booking["booking_number"] in replies[0]["message"]
    assert (await db.whatsapp_conversations.find_one({"wa_id": wa_id}))["state"] is None


async def test_bot_booking_sends_one_whatsapp_confirmation(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77710")
    _wa_cleanup(cleanup, wa_id, phone)
    convo = await drive_to_confirm(db, bot, rig, wa_id, new_name="One Confirm")
    cid = convo["customer_id"]
    _track(cleanup, cid)
    before = await _outbox_count(db, phone)
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    rows = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", 1).skip(before).to_list(None)
    assert [r for r in rows if r.get("kind") in ("notify", "event")] == []
    assert len([r for r in rows if "Booking confirmed!" in r["message"]]) == 1
    # The in-app half is untouched.
    # (In-app title is "Booking confirmed" since NTF-07 — it was "<service> booked".)
    assert await db.notifications.count_documents({"user_id": cid, "title": "Booking confirmed"}) == 1


# ---------------------------------------------------------------------------
# NTF-15: admin ping throttle, bot-collected name
# ---------------------------------------------------------------------------
async def test_free_text_pings_admins_once_per_conversation(db, cleanup, rig):
    if not await db.users.find_one({"role": "admin", "is_deleted": {"$ne": True}}):
        res = await db.users.insert_one({"full_name": "Ping Admin", "phone": "6000000771", "role": "admin", "is_deleted": False})
        cleanup.append(("users", {"_id": res.inserted_id}))
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77711")
    _wa_cleanup(cleanup, wa_id, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Many Questions"))
    _track(cleanup, str((await db.users.find_one({"phone": phone}))["_id"]))
    pings = {"title": HELP_TITLE, "message": {"$regex": f"^\\+{wa_id}:"}}

    await bot.handle_webhook(wa_payload(wa_id, text="do you clean sofas at home?"))
    first = await db.notifications.count_documents(pings)
    assert first >= 1
    for text in ("and carpets?", "what about bikes?", "hello??"):
        await bot.handle_webhook(wa_payload(wa_id, text=text))
    assert await db.notifications.count_documents(pings) == first
    assert "team has been notified" in (await db.whatsapp_outbox.find({"phone": phone}).sort("_id", -1).to_list(2))[1]["message"]

    # A resolved thread is a new conversation: the next question pings again.
    await WhatsAppCrmService(db).set_status(wa_id, "resolved")
    await bot.handle_webhook(wa_payload(wa_id, text="one more question please"))
    assert await db.notifications.count_documents(pings) == 2 * first

    # ...and so does one after the cooldown.
    old = datetime.now(timezone.utc) - timedelta(hours=ADMIN_PING_COOLDOWN_HOURS, minutes=5)
    await db.whatsapp_conversations.update_one({"wa_id": wa_id}, {"$set": {"admin_pinged_at": old}})
    await bot.handle_webhook(wa_payload(wa_id, text="still there?"))
    assert await db.notifications.count_documents(pings) == 3 * first


async def test_bot_collected_name_is_capped_and_cleaned(db, cleanup, rig):
    bot = WhatsAppBotService(db)
    wa_id, phone = _wa("77712")
    _wa_cleanup(cleanup, wa_id, phone)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Ravi\u0000 Kumar\u0007\n\tSharma " + "x" * 300))
    user = await db.users.find_one({"phone": phone})
    _track(cleanup, str(user["_id"]))
    name = user["full_name"]
    assert len(name) <= 100
    assert name.startswith("Ravi Kumar Sharma x")
    assert not any(ord(ch) < 32 for ch in name)


# ---------------------------------------------------------------------------
# EXT-4: Meta outage -> one attempt, no generic fallback
# ---------------------------------------------------------------------------
@pytest.fixture
def meta_cloud(db, cleanup, monkeypatch):
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PROVIDER", "meta_cloud")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_ACCESS_TOKEN", "x")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_UPDATE_TEMPLATE_NAME", "blussit_service_update")
    calls: list[str] = []

    def install(respond):
        class _FakeMeta:
            async def post(self, url, json=None, headers=None, **kw):
                name = ((json or {}).get("template") or {}).get("name") or (json or {}).get("type")
                calls.append(name)
                return await respond(name, url, json)

        monkeypatch.setattr(whatsapp_service, "_meta_http", lambda: _FakeMeta())
        return calls

    return install


async def _notify_captain_on_the_way(db, cleanup, phone):
    await db.whatsapp_templates.update_one(
        {"name": "blussit_captain_on_the_way_v6"},
        {"$set": {"status": "APPROVED", "param_count": 5, "has_url_param": True, "disabled": False, "fixntf": True}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": "blussit_captain_on_the_way_v6", "fixntf": True}))
    uid = str((await db.users.insert_one({"full_name": "Outage Cust", "phone": phone, "role": "customer", "is_deleted": False})).inserted_id)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("notifications", {"user_id": uid}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    await NotificationService(db).notify(
        uid, "Captain on the way", "msg", NotificationType.BOOKING, str(ObjectId()),
        wa_event="captain_on_the_way", wa_params=["a", "b", "c", "d", "e"],
    )
    return await db.whatsapp_outbox.find({"phone": phone}).sort("_id", 1).to_list(None)


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
async def test_meta_outage_makes_one_attempt_and_records_it(db, cleanup, meta_cloud, failure):
    async def respond(name, url, body):
        if failure == "timeout":
            raise httpx.ConnectTimeout("simulated Meta outage")
        return httpx.Response(503, json={"error": {"message": "Service unavailable"}}, request=httpx.Request("POST", url))

    calls = meta_cloud(respond)
    rows = await _notify_captain_on_the_way(db, cleanup, "6000000772" if failure == "timeout" else "6000000773")
    assert calls == ["blussit_captain_on_the_way_v6"]  # no generic retry into the same outage
    assert len(rows) == 1 and rows[0]["ok"] is False and rows[0]["failure"] == "transport"


async def test_rejected_event_template_still_falls_back_to_generic(db, cleanup, meta_cloud):
    async def respond(name, url, body):
        if name == "blussit_captain_on_the_way_v6":
            return httpx.Response(400, json={"error": {"code": 132000, "message": "Number of parameters does not match"}},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.FIXNTF{_RUN}gen"}]}, request=httpx.Request("POST", url))

    calls = meta_cloud(respond)
    rows = await _notify_captain_on_the_way(db, cleanup, "6000000774")
    assert calls == ["blussit_captain_on_the_way_v6", "blussit_service_update"]
    assert rows[0]["ok"] is False and rows[0]["failure"] == "rejected"
    assert rows[1]["ok"] is True and "failure" not in rows[1]


# ---------------------------------------------------------------------------
# NTF-8: OTPs and temp passwords are never stored or logged in clear
# ---------------------------------------------------------------------------
async def test_whatsapp_secrets_are_redacted_in_outbox_and_logs(db, cleanup, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    cleanup.append(("whatsapp_outbox", {"phone": {"$regex": "^600000078"}}))
    wa = WhatsAppService(db)
    assert await wa.send_otp("6000000781", "482913") is True
    assert await wa.send_temp_password("6000000782", "Zq81LmPx") is True
    monkeypatch.setattr(settings, "WHATSAPP_OTP_TEMPLATE_NAME", "blussit_otp")
    monkeypatch.setattr(settings, "WHATSAPP_TEMP_PASSWORD_TEMPLATE_NAME", "blussit_temp_password")
    assert await wa.send_otp("6000000783", "771144") is True
    assert await wa.send_temp_password("6000000784", "Pw55TtQq") is True

    rows = await db.whatsapp_outbox.find({"phone": {"$regex": "^600000078"}}).to_list(None)
    assert len(rows) == 4
    stored = " ".join(r["message"] for r in rows)
    for secret in ("482913", "Zq81LmPx", "771144", "Pw55TtQq"):
        assert secret not in stored
        assert secret not in caplog.text
    assert all("••••••" in r["message"] for r in rows)
    assert {r.get("template_name") for r in rows} == {None, "blussit_otp", "blussit_temp_password"}
    assert "OTP sent (••••••)" in caplog.text  # the log lines were captured, redacted


async def test_meta_provider_sends_the_code_but_stores_it_redacted(db, cleanup, meta_cloud):
    sent: list[dict] = []

    async def respond(name, url, body):
        sent.append(body)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.FIXNTF{_RUN}otp"}]}, request=httpx.Request("POST", url))

    meta_cloud(respond)
    cleanup.append(("whatsapp_outbox", {"phone": "6000000785"}))
    assert await WhatsAppService(db).send_otp("6000000785", "390817") is True
    assert "390817" in str(sent[0])  # the customer still gets the code
    row = await db.whatsapp_outbox.find_one({"phone": "6000000785"})
    assert "390817" not in row["message"] and row["ok"] is True


async def test_sms_secrets_are_redacted(db, cleanup, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    cleanup.append(("sms_outbox", {"phone": {"$regex": "^600000079"}}))
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")
    sms = sms_service.SmsService(db)
    assert await sms.send_otp("6000000791", "558812") is True
    assert await sms.send_temp_password("6000000792", "Hh72KkLl") is True

    class _Msg91:
        async def post(self, url, params=None, headers=None, **kw):
            return httpx.Response(200, json={"type": "success"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(sms_service, "shared_client", lambda *a, **k: _Msg91())
    assert await sms_service.Msg91Provider(db, "fake-key").send_otp("6000000793", "604417") is True

    rows = await db.sms_outbox.find({"phone": {"$regex": "^600000079"}}).to_list(None)
    assert {r["kind"] for r in rows} == {"otp", "temp_password"}
    stored = " ".join(r["message"] for r in rows)
    for secret in ("558812", "Hh72KkLl", "604417"):
        assert secret not in stored
        assert secret not in caplog.text
    assert "Temporary password sent (••••••)" in caplog.text


async def test_dev_tools_keep_secrets_readable_locally(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "DEV_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "APP_ENV", "development")
    if not settings.dev_tools_active:
        pytest.skip("dev tools need a database on this machine")
    monkeypatch.setattr(settings, "SMS_PROVIDER", "log")
    cleanup.append(("whatsapp_outbox", {"phone": "6000000794"}))
    cleanup.append(("sms_outbox", {"phone": "6000000795"}))
    await WhatsAppService(db).send_temp_password("6000000794", "Dd33EeFf")
    await sms_service.SmsService(db).send_otp("6000000795", "123456")
    assert "Dd33EeFf" in (await db.whatsapp_outbox.find_one({"phone": "6000000794"}))["message"]
    assert "123456" in (await db.sms_outbox.find_one({"phone": "6000000795"}))["message"]
