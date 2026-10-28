"""WhatsApp bot alignment (2026-10-28): the bot follows the SAME rules as the
web booking path, through the SAME service calls — holidays (warm message +
the next open days, on the day list, a stale tap, the real center, a
reschedule), "Use My Plan" (standard / custom / extended / two plans /
exhausted / scheduled), the cancel charge shown before cancelling, and a chat
linked to an account whose number is not the sender's. Local Mongo only."""
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingRescheduleRequest
from app.services.booking_service import BookingService
from app.services.whatsapp_bot_service import WhatsAppBotService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_feat_planfix_helpers as pf
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture
from tests.test_whatsapp_bot import wa_payload

pytestmark = pytest.mark.asyncio

_n = itertools.count(1)


async def _rig(db, clean) -> dict:
    """A 09:00-19:00 center with its manager, and a customer with a saved
    address there, who messages from their own number."""
    cid, pin, lat, lng = await hh.center(db, clean)
    mgr = await h.manager(db, cid)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"manager_id": mgr["id"]}})
    cu = await h.customer(db, pin)
    user = await db.users.find_one({"_id": ObjectId(cu["id"])})
    phone = user["phone"]
    return {
        "center_id": cid, "pin": pin, "mgr": mgr, "cu": cu, "customer": user, "phone": phone, "wa_id": f"91{phone}",
        "hatch": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db),
        "admin": await hh.admin_headers(db), "bot": WhatsAppBotService(db),
    }


async def say(s: dict, *, text=None, reply=None):
    await s["bot"].handle_webhook(wa_payload(s["wa_id"], text=text, reply=reply))


async def outbox(db, s: dict) -> list[dict]:
    return await db.whatsapp_outbox.find({"phone": s["phone"]}).sort("_id", 1).to_list(None)


async def last(db, s: dict) -> dict:
    return (await outbox(db, s))[-1]


def row_ids(msg: dict) -> list[str]:
    return [r["id"] for r in msg.get("options") or []]


async def to_when(db, s: dict) -> dict:
    """hi → book → hatchback → Star Wash; returns the next message (the plan
    question, or the day list when there is no plan)."""
    await say(s, text="hi")
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await say(s, reply=f"svc:{s['star']}")
    return await last(db, s)


async def holiday(db, s: dict, start: int, end: int, *, scope="centers", message="Diwali break 🪔") -> None:
    async with h.client() as c:
        kw = {"service_center_ids": [s["center_id"]]} if scope == "centers" else {}
        await hh.make(c, s["admin"], start_date=hh.day(start), end_date=hh.day(end), scope=scope, message=message, **kw)


# ---------------------------------------------------------------- holidays


async def test_day_list_skips_a_center_holiday_and_says_so_warmly(db, clean):
    s = await _rig(db, clean)
    await holiday(db, s, 1, 2)
    msg = await to_when(db, s)
    days = {r.split(":", 1)[1].split("|")[0] for r in row_ids(msg)}
    assert hh.day(1) not in days and hh.day(2) not in days
    assert hh.day(3) in days  # the next open day is offered instead
    body = msg["message"]
    assert "🌴" in body and "holiday break" in body and hh.long(hh.day(3)) in body and "Diwali break" in body
    assert "next open days" in body


async def test_all_centers_holiday_resume_date_and_nothing_to_book(db, clean):
    s = await _rig(db, clean)
    await holiday(db, s, 0, 1, scope="all")
    msg = await to_when(db, s)
    days = {r.split(":", 1)[1].split("|")[0] for r in row_ids(msg)}
    assert days and min(days) >= hh.day(2)
    assert hh.long(hh.day(2)) in msg["message"]


async def test_a_holiday_covering_the_whole_window_gives_the_sweet_message_only(db, clean):
    s = await _rig(db, clean)
    await holiday(db, s, 0, 9)
    msg = await to_when(db, s)
    assert msg.get("interactive_kind") is None
    assert "🌴" in msg["message"] and "holiday break" in msg["message"] and hh.long(hh.day(10)) in msg["message"]
    assert "booked" not in msg["message"].lower().replace("you can book", "")
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] is None


async def test_a_stale_tap_on_a_holiday_day_is_refused_warmly_and_reoffers_open_days(db, clean):
    s = await _rig(db, clean)
    msg = await to_when(db, s)  # no holiday yet — the list shows tomorrow
    assert any(r.startswith(f"when:{hh.day(1)}|") for r in row_ids(msg))
    await holiday(db, s, 1, 1)  # declared AFTER the list was sent
    await say(s, reply=f"when:{hh.day(1)}|09:00-12:00")
    sent = await outbox(db, s)
    assert "holiday break" in sent[-2]["message"] and hh.long(hh.day(2)) in sent[-2]["message"]
    again = sent[-1]
    assert again["interactive_kind"] == "list" and not any(r.startswith(f"when:{hh.day(1)}|") for r in row_ids(again))
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "q_when" and not convo["data"].get("date")
    assert await db.bookings.count_documents({"customer_id": s["cu"]["id"]}) == 0


async def test_a_holiday_declared_before_confirm_never_books(db, clean):
    s = await _rig(db, clean)
    await to_when(db, s)
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    assert (await last(db, s))["interactive_kind"] == "buttons"  # the confirm summary
    await holiday(db, s, 2, 2)
    await say(s, reply="confirm:yes")
    sent = await outbox(db, s)
    assert any("🌴" in m["message"] and hh.long(hh.day(3)) in m["message"] for m in sent[-3:])
    assert await db.bookings.count_documents({"customer_id": s["cu"]["id"]}) == 0
    assert await db.slot_holds.count_documents({"service_center_id": s["center_id"]}) == 0


async def test_reschedule_offers_only_open_days_and_a_stale_holiday_tap_is_warm(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(5), "09:00-12:00")
    await holiday(db, s, 1, 2)
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    await say(s, reply=f"bk:{booking['id']}")
    await say(s, reply="bkact:resched")
    msg = await last(db, s)
    ids = row_ids(msg)
    assert ids and not {f"rdate:{hh.day(1)}", f"rdate:{hh.day(2)}"} & set(ids)
    assert f"rdate:{hh.day(3)}" in ids and "🌴" in msg["message"]
    # A stale tap of a holiday day.
    await say(s, reply=f"rdate:{hh.day(1)}")
    msg = await last(db, s)
    assert "🌴" in msg["message"] and hh.long(hh.day(3)) in msg["message"]
    assert f"rdate:{hh.day(1)}" not in row_ids(msg)
    # An open day goes through and moves the booking.
    await say(s, reply=f"rdate:{hh.day(3)}")
    await say(s, reply="rslot:09:00-12:00")
    assert (await fb.doc(db, booking["id"]))["scheduled_slot"] == "09:00-12:00"
    assert (await fb.doc(db, booking["id"]))["scheduled_date"].strftime("%Y-%m-%d") == hh.day(3)


# ------------------------------------------------------------------- plans


async def _std_pass_rig(db, clean, washes=4):
    s = await _rig(db, clean)
    sub = await pf.std_pass(db, s["cu"]["id"], s["star"], car_id=s["cu"]["vehicle_id"], washes=washes)
    return s, sub


async def _book_through(db, s: dict, day: int, *, plan: str | None) -> dict:
    """Plan question (answered with `plan` button id) → day → saved address → Confirm."""
    q = await to_when(db, s)
    if plan is not None:
        assert any(i.startswith("plan:") for i in (row_ids(q) or [b["id"] for b in q.get("options") or []]))
        await say(s, reply=plan)
    await say(s, reply=f"when:{hh.day(day)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    return await db.bookings.find_one({"customer_id": s["cu"]["id"], "status": {"$ne": "cancelled"}})


async def test_one_plan_is_offered_and_booked_without_payment_and_restored_on_cancel(db, clean):
    s, sub = await _std_pass_rig(db, clean)
    q = await to_when(db, s)
    assert q["interactive_kind"] == "buttons"
    assert {b["id"] for b in q["options"]} == {f"plan:{sub}", "plan:none"}
    assert {b["title"] for b in q["options"]} == {"Use My Plan", "Pay Normal Price"}
    assert "4 washes left" in q["message"] and "Last Booking Day" in q["message"]
    await say(s, reply=f"plan:{sub}")
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    summary = await last(db, s)
    assert "₹0" in summary["message"] and "plan" in summary["message"].lower()
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    assert booking["subscription_id"] == sub and booking["total_amount"] == 0 and booking["source"] == "whatsapp"
    assert await fb.remaining(db, sub) == 3
    sent = await outbox(db, s)
    assert any("Covered by your plan" in m["message"] and "3 washes left" in m["message"] for m in sent)
    assert not any("How would you like to pay?" in m["message"] for m in sent)  # no payment link / pay buttons
    assert await db.payment_orders.count_documents({"booking_id": str(booking["_id"])}) == 0
    # Cancel through the bot: the wash goes back to the plan.
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    await say(s, reply=f"bk:{booking['_id']}")
    await say(s, reply="bkact:cancel")
    assert "plan wash goes back" in (await last(db, s))["message"]
    await say(s, reply="cxl:yes")
    assert (await fb.doc(db, str(booking["_id"])))["status"] == "cancelled"
    assert await fb.remaining(db, sub) == 4


async def test_pay_normal_price_keeps_the_wash(db, clean):
    s, sub = await _std_pass_rig(db, clean)
    booking = await _book_through(db, s, 2, plan="plan:none")
    assert booking.get("subscription_id") in (None, "") and booking["total_amount"] > 0
    assert await fb.remaining(db, sub) == 4


async def test_two_usable_plans_show_a_chooser_and_the_picked_one_is_used(db, clean):
    s = await _rig(db, clean)
    star = s["star"]
    second_car = await pf.extra_car(db, s["cu"]["id"])  # lets a car pass sit beside the type pass
    type_sub = await pf.std_pass(db, s["cu"]["id"], star, vehicle_type=s["hatch"], washes=4)
    car_sub = await pf.std_pass(db, s["cu"]["id"], star, car_id=s["cu"]["vehicle_id"], washes=2)
    del second_car
    q = await to_when(db, s)
    assert q["interactive_kind"] == "list"
    ids = row_ids(q)
    assert set(ids) == {f"plan:{type_sub}", f"plan:{car_sub}", "plan:none"}
    desc = {r["id"]: r.get("description", "") for r in q["options"]}
    assert "4 left" in desc[f"plan:{type_sub}"] and "2 left" in desc[f"plan:{car_sub}"]
    assert all("Last Booking Day" in desc[f"plan:{x}"] for x in (type_sub, car_sub))
    await say(s, reply=f"plan:{car_sub}")
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    assert booking["subscription_id"] == car_sub and booking["vehicle_id"] == s["cu"]["vehicle_id"]
    assert await fb.remaining(db, car_sub) == 1 and await fb.remaining(db, type_sub) == 4


async def test_a_custom_multi_car_pass_is_offered_by_name_and_consumed(db, clean):
    s = await _rig(db, clean)
    s["center_id"], s["mgr"] = s["center_id"], s["mgr"]
    sub = await pf.custom_pass(db, s, s["cu"]["vehicle_id"], [(s["star"], 3)])
    q = await to_when(db, s)
    assert f"plan:{sub}" in {b["id"] for b in q["options"]}
    await say(s, reply=f"plan:{sub}")
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"], "source": "whatsapp"})
    assert booking["subscription_id"] == sub and booking["total_amount"] == 0 and booking["vehicle_id"] == s["cu"]["vehicle_id"]
    assert (await pf.sub_doc(db, sub))["remaining_service_count"] == 2


async def test_an_extended_plan_is_offered_and_booked_inside_its_last_booking_day(db, clean):
    s, sub = await _std_pass_rig(db, clean)
    await pf.set_dates(db, sub, end=timedelta(hours=-12), extended=timedelta(days=5), start=timedelta(days=-31))
    q = await to_when(db, s)
    assert f"plan:{sub}" in {b["id"] for b in q["options"]}
    await say(s, reply=f"plan:{sub}")
    msg = await last(db, s)
    days = {r.split(":", 1)[1].split("|")[0] for r in row_ids(msg)}
    assert days and max(days) <= hh.day(4)  # nothing offered after the Last Booking Day
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    assert booking["subscription_id"] == sub and booking["total_amount"] == 0


async def test_an_exhausted_plan_says_why_and_books_at_the_normal_price(db, clean):
    s, sub = await _std_pass_rig(db, clean)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$set": {"remaining_service_count": 0, "status": "expired"}})
    msg = await to_when(db, s)
    texts = [m["message"] for m in (await outbox(db, s))]
    assert any("All washes used" in t and "normal price" in t for t in texts), texts
    assert msg["interactive_kind"] == "list" and any(r.startswith("when:") for r in row_ids(msg))  # straight on to the day
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    assert not booking.get("subscription_id") and booking["total_amount"] > 0


async def test_a_renewal_not_started_yet_is_not_offered(db, clean):
    s, sub = await _std_pass_rig(db, clean)
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(sub)}, {"$set": {"status": "scheduled", "start_date": __import__("app.utils.timezone", fromlist=["now_ist"]).now_ist() + timedelta(days=6)}}
    )
    msg = await to_when(db, s)
    assert not any(i.startswith("plan:") for i in row_ids(msg))
    assert any("Starts" in m["message"] for m in await outbox(db, s))


# ------------------------------------------------- cancel charge / identity


async def test_cancel_shows_the_charge_before_and_the_wallet_line_after(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(3), "09:00-12:00")
    await fb.slot_in(db, [booking["id"]], 120)  # inside the 1–4 h window
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    await say(s, reply=f"bk:{booking['id']}")
    await say(s, reply="bkact:cancel")
    prompt = (await last(db, s))["message"]
    preview = await BookingService(db).cancellation_charge_preview(booking["id"], s["cu"]["id"], "customer", None)
    assert preview["amount"] > 0 and f"₹{preview['amount']:g}" in prompt and "wallet" in prompt.lower()
    await say(s, reply="cxl:yes")
    assert (await fb.doc(db, booking["id"]))["status"] == "cancelled"


async def test_a_chat_linked_to_an_account_with_another_number_books_nothing_for_it(db, clean):
    s = await _rig(db, clean)
    stranger = f"9199{next(_n):08d}"[:12]
    await db.whatsapp_conversations.insert_one({"wa_id": stranger, "customer_id": s["cu"]["id"], "state": "menu", "data": {}, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    await s["bot"].handle_webhook(wa_payload(stranger, text="hi"))
    convo = await db.whatsapp_conversations.find_one({"wa_id": stranger})
    assert convo.get("customer_id") != s["cu"]["id"]
    out = await db.whatsapp_outbox.find({"phone": stranger[2:]}).to_list(None)
    assert out and "name" in out[-1]["message"].lower()  # asked who they are, like any new number
    await db.whatsapp_conversations.delete_many({"wa_id": stranger})
    await db.whatsapp_outbox.delete_many({"phone": stranger[2:]})


async def test_my_bookings_lists_only_the_senders_own(db, clean):
    s = await _rig(db, clean)
    other = await h.customer(db, s["pin"])
    mine = await fb.book(db, s["cu"], hh.day(2), "09:00-12:00")
    theirs = await fb.book(db, other, hh.day(3), "09:00-12:00")
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    ids = row_ids(await last(db, s))
    assert f"bk:{mine['id']}" in ids and f"bk:{theirs['id']}" not in ids
    await say(s, reply=f"bk:{theirs['id']}")  # a crafted id for someone else's booking
    assert "isn't on your account" in (await last(db, s))["message"] or "isn't on your account" in (await outbox(db, s))[-2]["message"]
