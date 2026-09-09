"""
WhatsApp booking bot — end-to-end conversation tests driven through the
exact same webhook payload shapes Meta delivers, all against the log
WhatsApp provider (forced by conftest) whose whatsapp_outbox records let
us assert precisely what the customer was shown.

The centralization guarantees these tests pin down:
  - a WhatsApp booking is a NORMAL booking (source tag aside): it reserves
    the same slot capacity counter web bookings use, and notifies the
    center's manager through the same path;
  - the slots offered in chat are BookingService.available_slots output —
    so an admin closing a slot is reflected in the very next chat message;
  - Meta webhook redeliveries (same wamid) never double-process — a
    retried "Confirm" tap can't create a second booking;
  - a WhatsApp number maps to exactly one customer account: existing
    accounts are linked (and become phone_verified — WhatsApp is proof of
    phone ownership), new numbers get an account auto-created.
"""
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.whatsapp_bot_service import WhatsAppBotService
from app.utils.timezone import now_ist

from tests.factories import (
    get_star_wash_service_id,
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
)

_wamid = itertools.count(1)


def wa_payload(wa_id: str, *, text=None, reply=None, location=None, name="WA Tester", wamid=None):
    if text is not None:
        msg = {"type": "text", "text": {"body": text}}
    elif reply is not None:
        msg = {"type": "interactive", "interactive": {"list_reply": {"id": reply, "title": "x"}}}
    elif location is not None:
        msg = {"type": "location", "location": {"latitude": location[0], "longitude": location[1]}}
    else:  # unsupported type (e.g. an image)
        msg = {"type": "image", "image": {"id": "media"}}
    msg["from"] = wa_id
    msg["id"] = wamid or f"wamid.TEST{next(_wamid):08d}"
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry-id",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "test"},
                    "contacts": [{"wa_id": wa_id, "profile": {"name": name}}],
                    "messages": [msg],
                },
            }],
        }],
    }


async def last_out(db, phone: str) -> dict:
    return await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(
        db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=5
    )
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    # Link the manager so create_booking's "notify the center manager"
    # branch fires — that's the "visible to the manager" guarantee.
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("notifications", {"user_id": manager_id}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": "^wamid.TEST"}}))
    return {"db": db, "center_id": center_id, "manager_id": manager_id, "hatchback": hatchback, "foam": foam}


def _register_wa_cleanup(cleanup, wa_id: str, phone: str):
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))


@pytest.mark.asyncio
async def test_full_booking_flow_from_a_brand_new_whatsapp_number(rig, db, cleanup):
    bot = WhatsAppBotService(db)
    wa_id, phone = "918887770001", "8887770001"
    _register_wa_cleanup(cleanup, wa_id, phone)

    # 1. First contact — account auto-created, menu offered.
    await bot.handle_webhook(wa_payload(wa_id, text="hi", name="Ravi Kumar"))
    user = await db.users.find_one({"phone": phone})
    assert user is not None
    cleanup.append(("users", {"_id": user["_id"]}))
    cleanup.append(("vehicles", {"owner_id": str(user["_id"])}))
    cleanup.append(("addresses", {"owner_id": str(user["_id"])}))
    cleanup.append(("bookings", {"customer_id": str(user["_id"])}))
    cleanup.append(("notifications", {"user_id": str(user["_id"])}))
    cleanup.append(("purchase_confirmations", {"customer_id": str(user["_id"])}))
    assert user["full_name"] == "Ravi Kumar"
    assert user["phone_verified"] is True  # WhatsApp IS the phone proof
    assert user["must_change_password"] is True  # claims web access via forgot-password
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "buttons"
    assert {b["id"] for b in out["options"]} == {"menu:book", "menu:bookings"}

    # 2. Book → no saved vehicles → vehicle-type list.
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "list"
    assert any(r["id"] == f"vt:{rig['hatchback']}" for r in out["options"])

    # 3. Type → brand/model → registration.
    await bot.handle_webhook(wa_payload(wa_id, reply=f"vt:{rig['hatchback']}"))
    await bot.handle_webhook(wa_payload(wa_id, text="Maruti Swift"))
    await bot.handle_webhook(wa_payload(wa_id, text="MP09WA0001"))
    vehicle = await db.vehicles.find_one({"owner_id": str(user["_id"])})
    assert vehicle and vehicle["registration_number"] == "MP09WA0001"

    # 4. Service list (with per-vehicle-type pricing in the description).
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "list"
    assert any(r["id"] == f"svc:{rig['foam']}" for r in out["options"])

    # 5. Pick service → live-location request (WhatsApp's native prompt).
    await bot.handle_webhook(wa_payload(wa_id, reply=f"svc:{rig['foam']}"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "location_request"

    # 6. Share pin → line1 → pincode; address is created and dispatch
    # resolves to the nearest covering center.
    await bot.handle_webhook(wa_payload(wa_id, location=(22.701, 75.801)))
    await bot.handle_webhook(wa_payload(wa_id, text="12, Test Colony, Main Road"))
    await bot.handle_webhook(wa_payload(wa_id, text="452099"))
    address = await db.addresses.find_one({"owner_id": str(user["_id"])})
    assert address and address["latitude"] == 22.701 and address["pincode"] == "452099"

    # 7. Date buttons → slot list must be exactly the same availability the
    # website would show for that center/date.
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "buttons"
    date_str = (now_ist().date() + timedelta(days=2)).isoformat()
    assert any(b["id"] == f"date:{date_str}" for b in out["options"])
    await bot.handle_webhook(wa_payload(wa_id, reply=f"date:{date_str}"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "list"
    offered = {r["id"] for r in out["options"]}
    web_slots = await BookingService(db).available_slots(rig["center_id"], date_str)
    assert offered == {f"slot:{s['key']}" for s in web_slots if s["status"] != "full"}

    # 8. Pick a slot → confirm summary → confirm.
    await bot.handle_webhook(wa_payload(wa_id, reply="slot:09:00-12:00"))
    out = await last_out(db, phone)
    assert out["interactive_kind"] == "buttons"
    assert {b["id"] for b in out["options"]} == {"confirm:yes", "confirm:no"}
    await bot.handle_webhook(wa_payload(wa_id, reply="confirm:yes"))

    booking = await db.bookings.find_one({"customer_id": str(user["_id"])})
    assert booking is not None
    assert booking["source"] == "whatsapp"
    assert booking["service_center_id"] == rig["center_id"]
    assert booking["scheduled_slot"] == "09:00-12:00"

    # Centralization: the SAME capacity counter web bookings use went down.
    slot_doc = await db.slot_capacity.find_one({"service_center_id": rig["center_id"], "date": date_str, "slot_key": "09:00-12:00"})
    assert slot_doc["booked_count"] == 1

    # Visible to the manager: same notification path as a web booking.
    notif = await db.notifications.find_one({"user_id": rig["manager_id"], "reference_id": str(booking["_id"])})
    assert notif is not None

    # The customer got a confirmation with the real booking number,
    # followed by the cash-or-online payment choice (the new final
    # exchange since Razorpay payment links landed).
    recent = await db.whatsapp_outbox.find({"phone": phone}, sort=[("_id", -1)]).to_list(length=2)
    assert "How would you like to pay?" in recent[0]["message"]
    assert booking["booking_number"] in recent[1]["message"]


@pytest.mark.asyncio
async def test_duplicate_webhook_delivery_is_ignored(rig, db, cleanup):
    bot = WhatsAppBotService(db)
    wa_id, phone = "918887770002", "8887770002"
    _register_wa_cleanup(cleanup, wa_id, phone)

    payload = wa_payload(wa_id, text="hi", wamid="wamid.TESTDUPLICATE01")
    first = await bot.handle_webhook(payload)
    user = await db.users.find_one({"phone": phone})
    cleanup.append(("users", {"_id": user["_id"]}))
    second = await bot.handle_webhook(payload)  # Meta redelivery, byte-identical
    assert first["processed"] == 1
    assert second["processed"] == 0
    # And no duplicate side effects: still exactly one account.
    assert await db.users.count_documents({"phone": phone}) == 1


@pytest.mark.asyncio
async def test_existing_customer_is_linked_not_duplicated(rig, db, cleanup):
    customer_id, _, _ = await make_customer_with_vehicle(db, rig["hatchback"], phone_verified=False)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    wa_id = f"91{customer['phone']}"
    _register_wa_cleanup(cleanup, wa_id, customer["phone"])

    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hello"))

    assert await db.users.count_documents({"phone": customer["phone"]}) == 1  # linked, not duplicated
    refreshed = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert refreshed["phone_verified"] is True  # messaging from the number proves ownership

    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["customer_id"] == customer_id


@pytest.mark.asyncio
async def test_admin_slot_closure_is_reflected_in_chat_offers(rig, db, cleanup):
    """Admin closes a slot through the normal admin path → the very next
    chat slot list must not offer it (one centralized system, not two)."""
    customer_id, _, _ = await make_customer_with_vehicle(db, rig["hatchback"])
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    wa_id, phone = f"91{customer['phone']}", customer["phone"]
    _register_wa_cleanup(cleanup, wa_id, phone)

    date_str = (now_ist().date() + timedelta(days=2)).isoformat()
    await BookingService(db).set_slot_capacity(rig["center_id"], date_str, "12:00-15:00", capacity=None, is_closed=True)

    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    out = await last_out(db, phone)
    vehicle = await db.vehicles.find_one({"owner_id": customer_id})
    assert any(r["id"] == f"veh:{vehicle['_id']}" for r in out["options"])  # saved vehicle offered directly
    await bot.handle_webhook(wa_payload(wa_id, reply=f"veh:{vehicle['_id']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"svc:{rig['foam']}"))
    out = await last_out(db, phone)
    address = await db.addresses.find_one({"owner_id": customer_id})
    assert any(r["id"] == f"addr:{address['_id']}" for r in out["options"])  # saved address offered directly
    await bot.handle_webhook(wa_payload(wa_id, reply=f"addr:{address['_id']}"))
    await bot.handle_webhook(wa_payload(wa_id, reply=f"date:{date_str}"))

    out = await last_out(db, phone)
    offered = {r["id"] for r in out["options"]}
    assert "slot:12:00-15:00" not in offered  # the admin-closed slot
    assert "slot:09:00-12:00" in offered  # the rest still bookable


@pytest.mark.asyncio
async def test_cancel_resets_the_conversation(rig, db, cleanup):
    bot = WhatsAppBotService(db)
    wa_id, phone = "918887770003", "8887770003"
    _register_wa_cleanup(cleanup, wa_id, phone)

    await bot.handle_webhook(wa_payload(wa_id, text="hi"))
    user = await db.users.find_one({"phone": phone})
    cleanup.append(("users", {"_id": user["_id"]}))
    await bot.handle_webhook(wa_payload(wa_id, reply="menu:book"))
    await bot.handle_webhook(wa_payload(wa_id, text="cancel"))

    convo = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert convo["state"] is None
    out = await last_out(db, phone)
    assert "cancelled" in out["message"].lower()


@pytest.mark.asyncio
async def test_webhook_verification_and_signature(monkeypatch, db):
    import hashlib
    import hmac as hmac_mod

    from app.core.config import settings
    from app.core.exceptions import ForbiddenException
    from app.routes.v1.whatsapp_webhook_routes import verify_webhook_signature, verify_webhook_subscription

    monkeypatch.setattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "secret-token")
    assert verify_webhook_subscription("subscribe", "secret-token", "challenge-123") == "challenge-123"
    with pytest.raises(ForbiddenException):
        verify_webhook_subscription("subscribe", "wrong-token", "challenge-123")
    # An unconfigured token must refuse verification, never accept anything.
    monkeypatch.setattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    with pytest.raises(ForbiddenException):
        verify_webhook_subscription("subscribe", "", "challenge-123")

    body = b'{"object":"whatsapp_business_account"}'
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "app-secret")
    good = "sha256=" + hmac_mod.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, good) is True
    assert verify_webhook_signature(body, "sha256=deadbeef") is False
    assert verify_webhook_signature(body, None) is False
    # No secret configured (dev) → checking is off.
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")
    assert verify_webhook_signature(body, None) is True


@pytest.mark.asyncio
async def test_delivery_status_webhook_marks_outbox_row(rig, db, cleanup):
    """Meta's `statuses` events are the ONLY place a silent drop is ever
    reported (send API says 200, delivery fails later) — a failed status
    must land on the matching outbox row instead of leaving it looking
    successful."""
    cleanup.append(("whatsapp_outbox", {"wamid": "wamid.TESTSTATUS01"}))
    await db.whatsapp_outbox.insert_one({"phone": "8887770009", "message": "otp", "ok": True, "wamid": "wamid.TESTSTATUS01"})

    bot = WhatsAppBotService(db)
    await bot.handle_webhook({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "statuses": [{
                "id": "wamid.TESTSTATUS01",
                "status": "failed",
                "recipient_id": "918887770009",
                "errors": [{"code": 131047, "title": "Re-engagement message", "message": "Re-engagement message"}],
            }],
        }}]}],
    })

    row = await db.whatsapp_outbox.find_one({"wamid": "wamid.TESTSTATUS01"})
    assert row["delivery_status"] == "failed"
    assert row["delivery_errors"][0]["code"] == 131047


@pytest.mark.asyncio
async def test_stateless_pay_buttons_send_link_or_cash_ack(db, cleanup, rig, monkeypatch):
    """The post-confirmation payment buttons work statelessly: cash gets a
    friendly ack; online flips the booking's method and drops a Razorpay
    payment link (stubbed client — no external call) into the chat."""
    from bson import ObjectId as _OID

    from app.services import payment_service as ps
    from app.services.whatsapp_bot_service import WhatsAppBotService

    class _Links:
        def create(self, payload):
            return {"id": "plink_bot_test", "short_url": "https://rzp.io/l/bot-test", **payload}

    class _Client:
        payment_link = _Links()

    monkeypatch.setattr(ps, "_razorpay_client", lambda: _Client())

    wa_id, phone = "919333000111", "9333000111"
    customer_id, _, _ = await make_customer_with_vehicle(db, rig["hatchback"])
    cleanup.append(("users", {"_id": _OID(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_message_dedup", {}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))
    await db.whatsapp_conversations.insert_one({"wa_id": wa_id, "customer_id": customer_id, "state": None, "data": {}})

    res = await db.bookings.insert_one({
        "booking_number": "BK-WAPAY", "customer_id": customer_id, "service_center_id": rig["center_id"],
        "status": "pending", "payment_method": "cash", "payment_status": "pending", "total_amount": 349.0,
        "scheduled_date": now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00", "is_deleted": False,
    })
    booking_id = str(res.inserted_id)
    cleanup.append(("bookings", {"_id": res.inserted_id}))

    bot = WhatsAppBotService(db)
    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:cash:{booking_id}"))
    out = await last_out(db, phone)
    assert "cash" in out["message"].lower()

    await bot.handle_webhook(wa_payload(wa_id, reply=f"pay:online:{booking_id}"))
    out = await last_out(db, phone)
    assert "https://rzp.io/l/bot-test" in out["message"]
    fresh = await db.bookings.find_one({"_id": res.inserted_id})
    assert fresh["payment_method"] == "online" and fresh["payment_status"] == "pending"
