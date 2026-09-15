"""
Bot hardening round: Indian registration-number validation, duplicate-car
reuse, readable My-bookings + in-chat reschedule/cancel (same BookingService
validations as the website), short booking numbers, and the two-strike
out-of-scope handoff to a human (admin ping + bot pause).
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.whatsapp_bot_service import WhatsAppBotService
from app.utils.timezone import now_ist
from app.utils.vehicle_reg import validate_indian_registration

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_service_center
from tests.test_whatsapp_bot import last_out, wa_payload


def test_registration_validator_accepts_real_indian_formats():
    assert validate_indian_registration("MP09AB1234") == "MP09AB1234"
    assert validate_indian_registration("dl1cxy9876") == "DL1CXY9876"  # Delhi style
    assert validate_indian_registration("mh 12 1234") == "MH121234"  # old no-series plate
    assert validate_indian_registration("22BH1234AB") == "22BH1234AB"  # Bharat series
    assert validate_indian_registration("KA-05-MN-4321") == "KA05MN4321"  # separators ignored
    assert validate_indian_registration("XX09AB1234") is None  # not a state code
    assert validate_indian_registration("MP09AB12") is None  # wrong number block
    assert validate_indian_registration("hello") is None
    assert validate_indian_registration("1234567890") is None


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(
        db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=5
    )
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": "^wamid.TEST"}}))
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "foam": foam}


def _register_wa_cleanup(cleanup, wa_id, phone):
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_inbox", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("users", {"phone": phone}))


async def _to_count_step(bot, db, wa_id, hatchback):
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="WA Tester"))  # brand-new number: name first
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{hatchback}"))


@pytest.mark.asyncio
async def test_invalid_count_is_reasked_then_typed_number_accepted(rig, db, cleanup):
    """"How many?" takes a tap (1/2/3) OR a typed number; anything else is
    re-asked once with the hint, and a typed "2" then moves on to the
    service list (with what's included sent as text first)."""
    wa_id, phone = "918887772001", "8887772001"
    _register_wa_cleanup(cleanup, wa_id, phone)
    bot = WhatsAppBotService(db)
    await _to_count_step(bot, db, wa_id, rig["hatchback"])

    await bot.handle_webhook(wa_payload(wa_id, text="lots"))
    out = await last_out(db, phone)
    assert "Tap 1, 2 or 3" in out["message"]
    await bot.handle_webhook(wa_payload(wa_id, text="2"))
    recent = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", -1).to_list(length=2)
    assert recent[0]["interactive_kind"] == "list"
    assert any(r["id"].startswith("svc:") for r in recent[0]["options"])
    assert "includes" in (recent[1].get("message") or "").lower()
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["data"]["cur"]["count"] == 2


@pytest.mark.asyncio
async def test_book_again_replays_the_last_visit(rig, db, cleanup):
    """A returning customer gets a "Book again" button; tapping it replays
    the last visit's vehicles, services and address and jumps straight to
    picking a time — no questions in between."""
    from datetime import datetime

    from app.schemas.booking_schema import QuickAddress, QuickBookingLine, QuickBookingRequest
    from app.services.auth_service import AuthService
    from app.services.booking_service import BookingService

    wa_id, phone = "918887772002", "8887772002"
    _register_wa_cleanup(cleanup, wa_id, phone)
    cleanup.append(("bookings", {"customer_phone": phone}))
    cleanup.append(("addresses", {"line1": "9 Again Street"}))
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Again Tester")
    when = (now_ist().date() + timedelta(days=1)).isoformat()
    await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Again Tester", customer_phone=phone,
            address=QuickAddress(line1="9 Again Street", pincode="452099"),
            lines=[QuickBookingLine(vehicle_type=rig["hatchback"], quantity=1, service_ids=[rig["foam"]])],
            scheduled_date=when, scheduled_slot="15:00-18:00",
        ),
        customer=customer, source="app", allow_pinless=True,
    )
    assert datetime.strptime(when, "%Y-%m-%d")

    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    out = await last_out(db, phone)
    assert "menu:again" in {b["id"] for b in out["options"]}
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:again"))
    recent = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", -1).to_list(length=2)
    assert recent[0]["interactive_kind"] == "list"
    assert all(r["id"].startswith("when:") for r in recent[0]["options"])
    assert "Same as last time" in (recent[1].get("message") or "")
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["state"] == "q_when"
    assert convo["data"]["lines"][0]["vehicle_type"] == rig["hatchback"]
    assert convo["data"]["address_id"]


@pytest.mark.asyncio
async def test_two_strikes_hand_off_to_admin_and_pause_bot(rig, db, cleanup):
    wa_id, phone = "918887772003", "8887772003"
    _register_wa_cleanup(cleanup, wa_id, phone)
    admin = await db.users.find_one({"role": "admin"})
    if admin:
        cleanup.append(("notifications", {"user_id": str(admin["_id"]), "title": "WhatsApp customer needs help"}))
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Strike Tester"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    # choose_vehicle/new-vehicle-type step; garbage twice -> human handoff.
    await bot.handle_webhook(wa_payload(wa_id, text="what is the meaning of life"))
    await bot.handle_webhook(wa_payload(wa_id, text="answer me!"))
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["bot_paused"] is True
    out = await last_out(db, phone)
    assert "real person" in out["message"]
    if admin:
        n = await db.notifications.find_one({"user_id": str(admin["_id"]), "title": "WhatsApp customer needs help"})
        assert n is not None


@pytest.mark.asyncio
async def test_out_of_menu_question_pings_admin_but_keeps_menu(rig, db, cleanup):
    wa_id, phone = "918887772004", "8887772004"
    _register_wa_cleanup(cleanup, wa_id, phone)
    admin = await db.users.find_one({"role": "admin"})
    if admin:
        cleanup.append(("notifications", {"user_id": str(admin["_id"]), "title": "WhatsApp customer needs help"}))
    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, text="Question Tester"))
    await bot.handle_webhook(wa_payload(wa_id, text="do you also clean sofas at home?"))
    outs = await db.whatsapp_outbox.find({"phone": phone}).sort("_id", -1).to_list(length=3)
    assert any("team has been notified" in (o.get("message") or "") for o in outs)
    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert not convo.get("bot_paused")  # menu still available, bot not paused


@pytest.mark.asyncio
async def test_my_bookings_readable_list_and_reschedule_flow(rig, db, cleanup):
    wa_id, phone = "918887772005", "8887772005"
    _register_wa_cleanup(cleanup, wa_id, phone)
    bot = WhatsAppBotService(db)

    # Book something first, through the bot itself (quick flow).
    await _to_count_step(bot, db, wa_id, rig["hatchback"])
    await bot.handle_webhook(wa_payload(wa_id, reply="cnt:1"))
    user = await db.users.find_one({"phone": phone})
    cleanup.append(("addresses", {"owner_id": str(user["_id"])}))
    cleanup.append(("bookings", {"customer_id": str(user["_id"])}))
    cleanup.append(("notifications", {"user_id": str(user["_id"])}))
    cleanup.append(("purchase_confirmations", {"customer_id": str(user["_id"])}))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"svc:{rig['foam']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply="more:no"))
    await bot.handle_webhook(wa_payload(wa_id, location=(22.701, 75.801)))
    await bot.handle_webhook(wa_payload(wa_id, text="12, Test Colony 452099"))
    out = await last_out(db, phone)
    when_id = out["options"][0]["id"]
    assert when_id.startswith("when:")
    await bot.handle_webhook(wa_payload(wa_id, reply=when_id))
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))
    booking = await db.bookings.find_one({"customer_id": str(user["_id"])})
    assert booking is not None
    # Short sequential booking number: BK0001-style.
    import re
    assert re.fullmatch(r"BK\d{4,}", booking["booking_number"])

    # My bookings -> interactive list with readable rows.
    await bot.handle_webhook(wa_payload(wa_id, text="menu"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:bookings"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "list"
    row = out["options"][0]
    assert booking["booking_number"] in row["title"]
    booked_svc = await db.services.find_one({"_id": ObjectId(rig["foam"])})
    assert booked_svc["name"] in row["description"] and "Pending" in row["description"]

    # Open details -> Reschedule -> new day -> new slot -> confirmed.
    await bot.handle_webhook(wa_payload(wa_id, reply=f"bk:{booking['_id']}"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "buttons"
    assert {b["id"] for b in out["options"]} == {"bkact:resched", "bkact:cancel", "bkact:back"}
    await bot.handle_webhook(wa_payload(wa_id, reply="bkact:resched"))
    new_date = (now_ist().date() + timedelta(days=1)).isoformat()
    await bot.handle_webhook(wa_payload(wa_id, reply=f"rdate:{new_date}"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "list"
    new_slot = out["options"][0]["id"].split(":", 1)[1]
    await bot.handle_webhook(wa_payload(wa_id, reply=out["options"][0]["id"]))

    fresh = await db.bookings.find_one({"_id": booking["_id"]})
    assert fresh["scheduled_slot"] == new_slot
    assert str(fresh["scheduled_date"])[:10] == new_date
    out = await last_out(db, phone)
    assert "Rescheduled" in out["message"]

    # Cancel flow with confirmation.
    await bot.handle_webhook(wa_payload(wa_id, text="menu"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:bookings"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"bk:{booking['_id']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply="bkact:cancel"))
    out = await last_out(db, phone)
    assert {b["id"] for b in out["options"]} == {"cxl:yes", "cxl:no"}
    await bot.handle_webhook(wa_payload(wa_id, reply="cxl:yes"))
    fresh = await db.bookings.find_one({"_id": booking["_id"]})
    assert fresh["status"] == "cancelled"
