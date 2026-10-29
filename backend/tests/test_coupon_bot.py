"""The WhatsApp booking bot's coupon step: Confirm / Have A Coupon Code /
Cancel, then the typed code — checked by the same quote the booking uses."""
import random

import pytest
from bson import ObjectId

from app.core.config import settings
from app.services.whatsapp_bot_service import WhatsAppBotService
from tests.factories import get_hatchback_type_id, get_star_wash_service_id
from tests.test_coupon_bundles_redeem import _center, _customer, _slot
from tests.test_coupon_rules import make_coupon
from tests.test_whatsapp_bot import last_out, wa_payload

pytestmark = pytest.mark.asyncio


async def rig(db, cleanup):
    center_id, pin = await _center(db, cleanup)
    c = await _customer(db, cleanup, pin)
    customer = await db.users.find_one({"_id": ObjectId(c[0])})
    phone, wa_id = customer["phone"], f"91{customer['phone']}"
    cleanup += [("whatsapp_outbox", {"phone": phone}), ("whatsapp_conversations", {"wa_id": wa_id}), ("whatsapp_inbox", {"wa_id": wa_id}),
                ("coupon_usages", {"user_id": c[0]})]
    hatch, star = await get_hatchback_type_id(db), await get_star_wash_service_id(db)
    when, slot = await _slot(db, center_id)
    line = {"vehicle_type": hatch, "type_name": "Hatchback", "count": 1, "quantity": 1, "service_ids": [star],
            "service_quantities": {}, "service_name": "Star Wash", "subtotal": 300.0}
    data = {"lines": [line], "address_id": c[2], "address_label": "Home", "center_id": center_id, "date": when, "slot": slot}
    bot = WhatsAppBotService(db)
    await bot._hold_and_confirm(wa_id, phone, c[0], dict(data))
    return bot, phone, wa_id, c[0]


async def say(bot, db, wa_id, phone, **kw):
    await bot.handle_webhook(wa_payload(wa_id, **kw))
    return await last_out(db, phone)


def ids(msg):
    return [o["id"] for o in msg.get("options") or []]


async def test_the_summary_has_three_buttons_and_a_valid_code_changes_the_total(db, cleanup):
    bot, phone, wa_id, cid = await rig(db, cleanup)
    code = await make_coupon(db, cleanup, value=100)
    out = await last_out(db, phone)
    assert ids(out) == ["confirm:yes", "confirm:coupon", "confirm:no"]
    assert [o["title"] for o in out["options"]][1] == "Have A Coupon Code"
    before = out["message"]

    out = await say(bot, db, wa_id, phone, reply="confirm:coupon")
    assert "type your coupon code" in out["message"].lower()
    # Any case / spacing is accepted.
    out = await say(bot, db, wa_id, phone, text=f"  {code[:3].lower()} {code[3:].lower()} ")
    assert "Coupon applied" in out["message"] and f"Coupon {code}: −₹100" in out["message"]
    assert ids(out) == ["confirm:yes", "confirm:remove", "confirm:no"]
    assert before.rstrip().endswith("💰 ₹349") and out["message"].rstrip().endswith("💰 ₹249")
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0  # a preview, nothing consumed

    out = await say(bot, db, wa_id, phone, reply="confirm:remove")
    assert ids(out) == ["confirm:yes", "confirm:coupon", "confirm:no"] and out["message"].rstrip().endswith("💰 ₹349")

    await say(bot, db, wa_id, phone, reply="confirm:coupon")
    await say(bot, db, wa_id, phone, text=code)
    await say(bot, db, wa_id, phone, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": cid})
    assert booking["coupon_code"] == code and booking["discount_amount"] == 100 and booking["total_amount"] == 249
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 1


async def test_an_invalid_code_says_so_with_the_three_buttons(db, cleanup):
    bot, phone, wa_id, cid = await rig(db, cleanup)
    plans_only = await make_coupon(db, cleanup, applies_to=["plans"])
    await say(bot, db, wa_id, phone, reply="confirm:coupon")
    out = await say(bot, db, wa_id, phone, text="NOT-A-CODE")
    assert out["message"].startswith("Invalid Coupon Code") and ids(out) == ["confirm:coupon", "confirm:yes", "confirm:no"]
    await say(bot, db, wa_id, phone, reply="confirm:coupon")
    out = await say(bot, db, wa_id, phone, text=plans_only)
    assert out["message"].startswith("Invalid Coupon Code") and "Not Valid For Bookings" in out["message"]
    # Retry or just continue: Confirm books it without a coupon.
    await say(bot, db, wa_id, phone, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": cid})
    assert booking and not booking.get("coupon_code")


async def test_bot_state_confusion_never_applies_or_consumes_a_code(db, cleanup):
    bot, phone, wa_id, cid = await rig(db, cleanup)
    code = await make_coupon(db, cleanup, total_usage_limit=1)
    # Typing a code when the bot didn't ask: a nudge, not an application.
    out = await say(bot, db, wa_id, phone, text=code)
    assert "Confirm" in out["message"] and "Coupon applied" not in out["message"]
    conv = await db.whatsapp_conversations.find_one({"wa_id": wa_id})
    assert not (conv.get("data") or {}).get("coupon_code")
    # A stale "have a coupon" tap after booking is just a menu.
    await say(bot, db, wa_id, phone, reply="confirm:yes")
    assert await db.bookings.count_documents({"customer_id": cid}) == 1
    out = await say(bot, db, wa_id, phone, text=code)
    assert ids(out)[:1] == ["menu:book"] or "menu" in str(out).lower()
    assert (await db.coupons.find_one({"code": code}))["total_used"] == 0
    out = await say(bot, db, wa_id, phone, reply="confirm:coupon")
    assert await db.bookings.count_documents({"customer_id": cid}) == 1
    # A second Confirm tap does not double-book.
    await say(bot, db, wa_id, phone, reply="confirm:yes")
    assert await db.bookings.count_documents({"customer_id": cid}) == 1


async def test_a_coupon_taken_between_preview_and_confirm_drops_out_and_the_summary_returns(db, cleanup):
    bot, phone, wa_id, cid = await rig(db, cleanup)
    code = await make_coupon(db, cleanup, total_usage_limit=1)
    await say(bot, db, wa_id, phone, reply="confirm:coupon")
    await say(bot, db, wa_id, phone, text=code)
    await db.coupons.update_one({"code": code}, {"$set": {"total_used": 1}})  # someone else took the last use
    out = await say(bot, db, wa_id, phone, reply="confirm:yes")
    assert "can't be used any more" in out["message"] and ids(out) == ["confirm:yes", "confirm:coupon", "confirm:no"]
    assert await db.bookings.count_documents({"customer_id": cid}) == 0
    out = await say(bot, db, wa_id, phone, reply="confirm:yes")
    assert (await db.bookings.find_one({"customer_id": cid}))["total_amount"] == 349


async def test_bot_flooding_with_wrong_codes_is_held(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "COUPON_MISS_LIMIT", 3)
    cleanup.append(("rate_counters", {"_id": {"$regex": "^coupon-miss"}}))
    bot, phone, wa_id, cid = await rig(db, cleanup)
    good = await make_coupon(db, cleanup)
    for i in range(3):
        await say(bot, db, wa_id, phone, reply="confirm:coupon")
        out = await say(bot, db, wa_id, phone, text=f"WRONG{i}")
        assert out["message"].startswith("Invalid Coupon Code")
    await say(bot, db, wa_id, phone, reply="confirm:coupon")
    out = await say(bot, db, wa_id, phone, text=good)
    assert "Too Many Coupon Attempts" in out["message"]
    # Nothing was keyed on the shared webhook address.
    assert not await db.rate_counters.find_one({"_id": {"$regex": "^coupon-miss:i:"}})
