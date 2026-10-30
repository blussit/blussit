"""WhatsApp bot — understanding what the customer TYPED (2026-10-29).

The deterministic intent layer (services/whatsapp_intent.py) and how the bot
uses it: prices from the live catalogue, plans only for customers who have
one (and never SOLD in chat — the website link instead), cancel / reschedule
/ status / timing / help / "agent" in English, Hindi and Hinglish, typed
answers to buttons, and every side question leaving the booking draft and
the current step intact. Local Mongo only."""
import itertools
import re

import pytest
from bson import ObjectId

from app.services import whatsapp_intent as wi
from app.services.booking_service import BookingService
from app.services.whatsapp_bot_service import WhatsAppBotService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_feat_planfix_helpers as pf
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture
from tests.test_whatsapp_bot import wa_payload
from tests.test_whatsapp_bot_alignment import _rig, last, outbox, row_ids, say, to_when

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------ pure intents


@pytest.mark.parametrize("text,expected", [
    ("kitna rate hai", wi.PRICE), ("Sedan ka rate", wi.PRICE), ("price list please", wi.PRICE), ("kya rate hai?", wi.PRICE),
    ("charges?", wi.PRICE), ("how much for hatchback", wi.PRICE), ("कितना", wi.PRICE), ("rate", wi.PRICE),
    ("book karna hai", wi.BOOK), ("I want to book a wash", wi.BOOK), ("booking karni hai", wi.BOOK), ("wash", wi.BOOK),
    ("cancel my booking", wi.CANCEL), ("booking cancel karni hai", wi.CANCEL),
    ("reschedule", wi.RESCHEDULE), ("change my time", wi.RESCHEDULE), ("booking badalni hai", wi.RESCHEDULE),
    ("status", wi.STATUS), ("kab aayega captain", wi.STATUS), ("where is my captain", wi.STATUS), ("my bookings", wi.STATUS),
    ("address", wi.ADDRESS), ("aap kahan ho", wi.ADDRESS),
    ("timing", wi.TIMING), ("sunday open hai?", wi.TIMING), ("kitne baje tak", wi.TIMING), ("kitna time lagega", wi.TIMING),
    ("offer", wi.OFFER), ("coupon", wi.OFFER), ("discount milega?", wi.OFFER),
    ("plan", wi.PLAN_BUY), ("buy plan", wi.PLAN_BUY), ("monthly pass lena hai", wi.PLAN_BUY), ("renew my plan", wi.PLAN_BUY),
    ("subscription", wi.PLAN_BUY), ("membership", wi.PLAN_BUY), ("plan kitne ka hai", wi.PLAN_BUY),
    ("my plan", wi.PLAN_STATUS), ("washes left in my plan", wi.PLAN_STATUS), ("mera plan kitna bacha hai", wi.PLAN_STATUS),
    ("help", wi.HELP), ("what can you do", wi.HELP),
    ("agent", wi.HUMAN), ("I want to talk to manager", wi.HUMAN), ("human please", wi.HUMAN), ("refund chahiye", wi.HUMAN),
    ("captain nahi aaya", wi.HUMAN), ("complaint", wi.HUMAN),
    ("thanks", wi.THANKS), ("thank you!", wi.THANKS), ("shukriya", wi.THANKS), ("ok thanks", wi.THANKS),
    ("what is the meaning of life", None), ("do you also clean sofas at home?", None), ("answer me!", None), ("hatchback wash", None),
])
def test_intent_table(text, expected):
    assert wi.detect_intent(text) == expected


@pytest.mark.parametrize("text", ["hi", "Hii", "Hello 🙏", "namaste", "Good morning", "hey there", "menu"])
def test_greetings(text):
    assert wi.is_greeting(text)


def test_a_long_story_is_left_to_a_human_and_plans_are_never_a_booking_intent():
    assert wi.detect_intent("my car " + "was washed badly " * 30) is None
    assert wi.detect_intent("book a plan") == wi.PLAN_BUY  # "plan" wins over "book": never sold in chat


def test_match_names_handles_aliases_prefixes_and_nested_names():
    names = {"a": "Compact SUV", "b": "SUV / XUV", "c": "Hatchback", "d": "Sedan"}
    assert wi.match_names("compact suv ka rate", names) == ["a"]
    assert wi.match_names("suv rate", names) == ["b"]
    assert wi.match_names("xuv", names) == ["b"]
    assert wi.match_names("hatch price", names) == ["c"]
    assert wi.match_names("sedan aur hatchback", names) == ["c", "d"] or set(wi.match_names("sedan aur hatchback", names)) == {"c", "d"}
    assert wi.match_names("price list", names) == []


def test_typed_answers_and_message_splitting():
    assert wi.yes_no("Haan") == "yes" and wi.yes_no("confirm!") == "yes" and wi.yes_no("nahi") == "no" and wi.yes_no("maybe") is None
    assert wi.plan_answer("use my plan") == "use" and wi.plan_answer("normal price") == "normal" and wi.plan_answer("hmm") is None
    assert wi.wants_coupon("I have a coupon") and not wi.wants_coupon("yes")
    blocks = [f"*Type {i}*\n" + "• line\n" * 200 for i in range(6)]
    parts = wi.split_messages(blocks, limit=4096)
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)
    assert "".join(parts).count("*Type") == 6  # nothing lost
    assert wi.plan_link().startswith("https://") and wi.plan_link().endswith("/app/subscriptions")


# ------------------------------------------------------------ the bot


async def _types(db) -> list[dict]:
    return await db.vehicle_types.find({"is_active": True, "is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(None)


async def _texts(db, s: dict, since: int) -> list[dict]:
    return (await outbox(db, s))[since:]


async def _after_hi(db, clean_, extra=None):  # noqa: ANN001
    s = await _rig(db, clean_)
    await say(s, text="hi")
    return s


async def test_price_question_asks_the_vehicle_then_the_service_then_shows_that_price(db, clean):
    s = await _after_hi(db, clean)
    before = len(await outbox(db, s))
    await say(s, text="kitna rate hai")
    sent = await _texts(db, s, before)
    # No catalogue dump: ONE short question, a list of vehicle types.
    assert len(sent) == 1 and sent[0]["interactive_kind"] == "list" and len(sent[0]["message"]) < 200
    assert row_ids(sent[0]) == [f"pt:{t['_id']}" for t in (await _types(db))[:10]]
    # The vehicle -> the services, each row already carrying its price.
    await say(s, reply=f"pt:{s['hatch']}")
    services = await last(db, s)
    assert services["interactive_kind"] == "list" and f"ps:{s['hatch']}:{s['star']}" in row_ids(services)
    star = await db.services.find_one({"_id": ObjectId(s["star"])})
    first_time = await BookingService(db)._first_time_eligible(s["phone"])
    expected = BookingService.price_view(star, s["hatch"], first_time)["price"]
    row = next(r for r in services["options"] if r["id"] == f"ps:{s['hatch']}:{s['star']}")
    assert f"₹{expected:g}" in row["description"]
    # The service -> its price and Book Now.
    await say(s, reply=f"ps:{s['hatch']}:{s['star']}")
    card = await last(db, s)
    assert card["interactive_kind"] == "buttons" and f"₹{expected:g}" in card["message"] and star["name"] in card["message"]
    assert [b["id"] for b in card["options"]][0] == f"pbook:{s['hatch']}:{s['star']}"
    assert [b["title"] for b in card["options"]] == ["Book Now", "Other Service", "Other Vehicle"]
    assert "distance" not in card["message"].lower()  # that depends on the location: shown with the booking summary
    # Asking prices never touched the conversation state.
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] in (None, "menu")
    # Book Now continues straight into the booking with that vehicle + service chosen.
    await say(s, reply=f"pbook:{s['hatch']}:{s['star']}")
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] not in (None, "menu") and (await last(db, s))["interactive_kind"] in ("list", "buttons")
    assert convo["data"]["cur"]["vehicle_type"] == s["hatch"] or convo["data"]["lines"]


async def test_price_for_one_named_type_skips_the_vehicle_question(db, clean):
    s = await _after_hi(db, clean)
    sedan = next(t for t in await _types(db) if "sedan" in t["name"].lower())
    before = len(await outbox(db, s))
    await say(s, text="sedan ka rate batao")
    sent = await _texts(db, s, before)
    assert len(sent) == 1 and sent[0]["interactive_kind"] == "list"
    assert all(r.startswith(f"ps:{sedan['_id']}:") for r in row_ids(sent[0]))
    svc = await db.services.find_one({"_id": ObjectId(s["star"])})
    view = BookingService.price_view(svc, str(sedan["_id"]), await BookingService(db)._first_time_eligible(s["phone"]))
    desc = " ".join(r["description"] for r in sent[0]["options"] if r.get("description"))
    assert f"₹{view['price']:g}" in desc


async def test_a_price_question_mid_booking_answers_then_asks_the_same_question_again(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "q_service"
    before = len(await outbox(db, s))
    await say(s, text="price kya hai?")
    sent = await _texts(db, s, before)
    assert any("price is shown with each option" in m["message"] for m in sent)  # one line, no catalogue dump
    assert not any(m["message"].startswith("💰 *Our Prices*") for m in sent)
    assert sent[-1]["interactive_kind"] == "list" and f"svc:{s['star']}" in row_ids(sent[-1])  # the service list again
    after = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert after["state"] == "q_service" and after["data"]["cur"]["vehicle_type"] == s["hatch"] and not after["data"].get("strikes")
    await say(s, reply=f"svc:{s['star']}")  # the booking carries on
    assert (await last(db, s))["interactive_kind"] == "list"


async def test_no_plan_means_no_plan_question_and_no_mention_of_plans(db, clean):
    s = await _rig(db, clean)
    msg = await to_when(db, s)
    assert msg["interactive_kind"] == "list" and any(r.startswith("when:") for r in row_ids(msg))  # straight on to the day
    texts = " ".join(m["message"].lower() for m in await outbox(db, s))
    assert "plan" not in texts and "🎟" not in texts
    assert not any(i.startswith("plan:") for m in await outbox(db, s) for i in row_ids(m))


async def test_a_plan_is_offered_booked_once_and_not_offered_again_when_used_up(db, clean):
    s = await _rig(db, clean)
    sub = await pf.std_pass(db, s["cu"]["id"], s["star"], car_id=s["cu"]["vehicle_id"], washes=1)
    q = await to_when(db, s)
    assert {b["id"] for b in q["options"]} == {f"plan:{sub}", "plan:none"} and "1 wash left" in q["message"]
    await say(s, reply=f"plan:{sub}")
    await say(s, reply="ext:no")  # no extras
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, text="yes")  # typed "yes" at the summary confirms
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    assert booking["subscription_id"] == sub and booking["total_amount"] == 0
    assert await fb.remaining(db, sub) == 0
    # The one wash is spent: a second booking is offered no plan and prices normally.
    await say(s, text="hi")
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await say(s, reply=f"svc:{s['star']}")
    nxt = await last(db, s)
    assert not any(i.startswith("plan:") for i in row_ids(nxt)) and nxt["interactive_kind"] == "list"


async def test_buying_a_plan_sends_the_website_link_never_a_chat_sale(db, clean):
    s = await _after_hi(db, clean)
    for text in ("plan lena hai", "monthly pass", "subscription kitne ka hai", "renew plan"):
        before = len(await outbox(db, s))
        await say(s, text=text)
        sent = await _texts(db, s, before)
        assert any(wi.plan_link() in m["message"] and "website" in m["message"] for m in sent), text
        assert sent[-1]["interactive_kind"] == "buttons" and {b["id"] for b in sent[-1]["options"]} == {"menu:book", "menu:bookings"}
        assert not any(i.startswith(("plan:", "sub:", "buy:")) for m in sent for i in row_ids(m))
    assert await db.user_subscriptions.count_documents({"customer_id": s["cu"]["id"]}) == 0
    assert await db.payment_orders.count_documents({"customer_id": s["cu"]["id"]}) == 0


async def test_plan_link_mid_booking_keeps_the_draft(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await say(s, text="I want to buy a plan")
    sent = await outbox(db, s)
    assert any(wi.plan_link() in m["message"] for m in sent)
    assert sent[-1]["interactive_kind"] == "list"  # the service question again
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "q_service" and convo["data"]["cur"]["vehicle_type"] == s["hatch"]


async def test_my_plan_question_reads_the_plan_and_buying_text_names_an_existing_one(db, clean):
    s = await _after_hi(db, clean)
    await say(s, text="mera plan kitna bacha hai")
    assert "don't have an active plan" in [m for m in await outbox(db, s) if not m.get("interactive_kind")][-1]["message"]
    sub = await pf.std_pass(db, s["cu"]["id"], s["star"], car_id=s["cu"]["vehicle_id"], washes=3)
    await say(s, text="how many washes left in my plan")
    text = [m for m in await outbox(db, s) if not m.get("interactive_kind")][-1]["message"]
    assert "3 washes left" in text and wi.plan_link() in text
    await say(s, text="plan")
    text = [m for m in await outbox(db, s) if not m.get("interactive_kind")][-1]["message"]
    assert "already have" in text and "3 washes left" in text and wi.plan_link() in text
    del sub


async def test_cancel_by_words_goes_to_the_booking_and_asks_before_cancelling(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(3), "09:00-12:00")
    number = (await fb.doc(db, booking["id"]))["booking_number"]
    await say(s, text="hi")
    await say(s, text="cancel")  # a bare "cancel" with nothing being built is about the booking
    detail = await last(db, s)
    assert number in detail["message"] and "bkact:cancel" in [b["id"] for b in detail["options"]]
    assert [b["title"] for b in detail["options"]][1] == "❌ Cancel Booking"
    await say(s, text="cancel")  # on the booking's page: cancel THIS booking -> the prompt
    prompt = await last(db, s)
    assert [b["id"] for b in prompt["options"]] == ["cxl:yes", "cxl:no"]
    await say(s, text="haan")  # typed yes
    assert (await fb.doc(db, booking["id"]))["status"] == "cancelled"
    done = await last(db, s)
    assert number in done["message"] and "cancelled" in done["message"].lower()


async def test_reschedule_and_status_questions_open_the_booking(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(3), "09:00-12:00")
    await say(s, text="hi")
    await say(s, text="mujhe booking reschedule karni hai")
    assert "bkact:resched" in [b["id"] for b in (await last(db, s))["options"]]
    await say(s, text="reschedule")  # typed on the page -> the new-day buttons
    assert any(i.startswith("rdate:") for i in [b["id"] for b in (await last(db, s))["options"]])
    await say(s, text="menu")
    await say(s, text="kab aayega captain?")
    last_msg = await last(db, s)
    assert "Captain" in last_msg["message"] and booking["id"] in str(await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))


async def test_cancel_with_no_bookings_says_so_and_offers_the_menu(db, clean):
    s = await _after_hi(db, clean)
    await say(s, text="cancel my booking")
    sent = await outbox(db, s)
    assert "no upcoming bookings" in sent[-2]["message"] and sent[-1]["interactive_kind"] == "buttons"


async def test_cancel_while_building_a_booking_drops_the_draft_and_says_how_to_manage_bookings(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, text="cancel")
    sent = await outbox(db, s)
    assert "nothing was booked" in sent[-1]["message"] and "my bookings" in sent[-1]["message"]
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] is None


async def test_typed_vehicle_type_service_and_confirm_answers(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, text="hatchback")
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] == "q_service"
    star = await db.services.find_one({"_id": ObjectId(s["star"])})
    await say(s, text=star["name"].lower())
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] in ("q_when", "q_plan")
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    summary = await last(db, s)
    assert "2026" not in summary["message"].split("📅")[1].split("\n")[0]  # "Sat 31 Oct", not an ISO date
    await say(s, text="coupon hai")  # asks for the code, like the button
    assert "coupon code" in (await last(db, s))["message"].lower()
    await say(s, text="cancel")  # leaves the coupon question and the draft
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] is None


async def test_a_car_type_named_at_the_menu_starts_the_booking_for_it(db, clean):
    s = await _after_hi(db, clean)
    sedan = next(t for t in await _types(db) if "sedan" in t["name"].lower())
    await say(s, text="sedan")
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "q_service" and convo["data"]["cur"]["vehicle_type"] == str(sedan["_id"])


async def test_asking_for_a_person_hands_over_and_pauses_the_bot(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, text="I want to talk to your manager")
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["bot_paused"] is True
    assert "real person" in (await last(db, s))["message"]


async def test_timing_address_offer_help_and_thanks_each_answer_and_show_the_menu(db, clean):
    s = await _after_hi(db, clean)
    center = await db.service_centers.find_one({"_id": ObjectId(s["center_id"])})
    for text, needle in (
        ("timing kya hai", "captains work between"),
        ("aap kahan ho", "We come to you"),
        ("koi offer hai?", "today's offers"),
        ("help", "Here's what I can do"),
        ("thanks", "You're welcome"),
    ):
        before = len(await outbox(db, s))
        await say(s, text=text)
        sent = await _texts(db, s, before)
        assert any(needle in m["message"] for m in sent), (text, [m["message"][:60] for m in sent])
        if text == "koi offer hai?":  # offers ask the vehicle first (a list), prices never dumped
            assert sent[-1]["interactive_kind"] == "list" and all(r.endswith(":o") for r in row_ids(sent[-1])), text
        elif text != "thanks":  # a thank-you needs no next step; every answer does
            assert sent[-1]["interactive_kind"] == "buttons", text
    assert center  # hours come from the center (7:00 AM style 12-hour times)


async def test_unknown_text_at_the_menu_still_notifies_the_team(db, clean):
    s = await _after_hi(db, clean)
    await say(s, text="do you also clean sofas at home?")
    sent = await outbox(db, s)
    assert "team has been notified" in sent[-2]["message"] and "prices" in sent[-2]["message"]
    assert sent[-1]["interactive_kind"] == "buttons"


async def test_a_reaction_gets_no_reply_but_a_photo_gets_an_honest_one_and_the_step_back(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    before = len(await outbox(db, s))
    payload = wa_payload(s["wa_id"], text="x")
    payload["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": s["wa_id"], "id": "wamid.TESTREACT" + str(next(itertools.count(1))) + "zz", "type": "reaction", "reaction": {"emoji": "👍", "message_id": "x"},
    }
    await s["bot"].handle_webhook(payload)
    assert len(await outbox(db, s)) == before  # nothing sent for a 👍
    await s["bot"].handle_webhook(wa_payload(s["wa_id"]))  # an image
    sent = await _texts(db, s, before)
    assert "can't open photos" in sent[0]["message"] and sent[-1]["interactive_kind"] == "list"  # the vehicle list again
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] == "q_type"


async def test_an_expired_button_tap_says_so_instead_of_a_silent_menu(db, clean):
    s = await _after_hi(db, clean)
    await db.whatsapp_conversations.update_one({"wa_id": s["wa_id"]}, {"$set": {"state": None, "data": {}}})
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    sent = await outbox(db, s)
    assert "expired" in sent[-2]["message"] and sent[-1]["interactive_kind"] == "buttons"
    await db.whatsapp_conversations.update_one({"wa_id": s["wa_id"]}, {"$set": {"state": None, "data": {}}})
    await say(s, reply="menu:book")  # the menu's own buttons keep working
    assert any(r.startswith("vt:") for r in row_ids(await last(db, s)))


async def test_typed_address_instead_of_a_pin_is_explained_then_handed_to_a_human(db, clean):
    s = await _rig(db, clean)
    await db.addresses.delete_many({"owner_id": s["cu"]["id"]})  # no saved address -> the bot asks for a pin
    await to_when(db, s)
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    assert (await last(db, s))["interactive_kind"] == "location_request"
    await say(s, text="Flat 12, Vijay Nagar, Indore")
    assert "attach" in (await last(db, s))["message"] and "Send Location" in (await last(db, s))["message"]
    await say(s, text="Flat 12, Vijay Nagar, Indore 452010")
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})).get("bot_paused") is True


async def test_reschedule_confirmation_names_the_booking_day_and_time_in_plain_words(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(5), "09:00-12:00")
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    await say(s, reply=f"bk:{booking['id']}")
    await say(s, reply="bkact:resched")
    await say(s, reply=f"rdate:{hh.day(3)}")
    await say(s, reply="rslot:09:00-12:00")
    done = (await last(db, s))["message"]
    number = (await fb.doc(db, booking["id"]))["booking_number"]
    assert "Rescheduled" in done and number in done and "9:00 AM – 12:00 PM" in done and "2026-" not in done and "my bookings" in done


def _is_title_case(title: str) -> bool:
    words = [w for w in re.sub(r"^[^\w]+", "", title).split() if w[0].isalpha()]
    return all(w[0].isupper() for w in words)


async def test_every_button_and_list_label_is_title_case_and_within_meta_limits(db, clean):
    s = await _rig(db, clean)
    booking = await fb.book(db, s["cu"], hh.day(3), "09:00-12:00")
    await say(s, text="hi")
    await say(s, reply="menu:bookings")
    await say(s, reply=f"bk:{booking['id']}")
    await say(s, reply="bkact:cancel")
    await say(s, reply="cxl:no")
    await say(s, text="hi")
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await say(s, reply=f"svc:{s['star']}")
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:yes")
    seen = 0
    for m in await outbox(db, s):
        for o in m.get("options") or []:
            seen += 1
            limit = 20 if m["interactive_kind"] == "buttons" else 24
            assert len(o["title"]) <= limit and _is_title_case(o["title"]), (m["interactive_kind"], o["title"])
    assert seen >= 10


async def test_the_bot_has_no_path_that_sells_a_plan():
    import inspect

    from app.services import whatsapp_bot_service as bot_module

    source = inspect.getsource(bot_module)
    assert ".subscribe(" not in source and "SubscribeRequest" not in source and "create_subscription" not in source


async def test_a_brand_new_number_asking_a_price_is_answered_before_the_name_is_asked(db, clean):
    s = await _rig(db, clean)
    await db.users.delete_one({"_id": ObjectId(s["cu"]["id"])})  # the number has no account now
    await db.whatsapp_conversations.delete_many({"wa_id": s["wa_id"]})
    await say(s, text="kitna rate hai?")
    sent = await outbox(db, s)
    assert any(m.get("interactive_kind") == "list" and any(r.startswith("pt:") for r in row_ids(m)) for m in sent)
    assert "tell me your name" in sent[-1]["message"]
    assert await db.users.count_documents({"phone": s["phone"]}) == 0  # still no account
    await say(s, reply=f"pt:{s['hatch']}")  # browsing prices works before there is an account...
    assert (await last(db, s))["interactive_kind"] == "list" and await db.users.count_documents({"phone": s["phone"]}) == 0
    await say(s, reply=f"pbook:{s['hatch']}:{s['star']}")  # ...and Book Now asks the name instead of booking
    assert "tell me your name" in (await last(db, s))["message"] and await db.users.count_documents({"phone": s["phone"]}) == 0
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "collect_name"
    await say(s, text="price list")  # a question is never taken as the name
    assert await db.users.count_documents({"phone": s["phone"]}) == 0
    await say(s, text="Ravi Kumar")
    user = await db.users.find_one({"phone": s["phone"]})
    assert user and user["full_name"] == "Ravi Kumar"
    await db.users.delete_one({"_id": user["_id"]})


async def test_an_unfinished_booking_that_timed_out_is_explained_not_silently_dropped(db, clean):
    from datetime import datetime, timedelta, timezone

    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await db.whatsapp_conversations.update_one({"wa_id": s["wa_id"]}, {"$set": {"updated_at": datetime.now(timezone.utc) - timedelta(minutes=90)}})
    await say(s, text="yes")
    sent = await outbox(db, s)
    assert "cleared that unfinished booking" in sent[-2]["message"] and sent[-1]["interactive_kind"] == "buttons"


async def test_no_coupon_at_the_coupon_question_returns_to_the_summary(db, clean):
    s = await _rig(db, clean)
    await to_when(db, s)
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    await say(s, reply="confirm:coupon")
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] == "q_coupon"
    await say(s, text="no coupon")
    summary = await last(db, s)
    assert {b["id"] for b in summary["options"]} == {"confirm:yes", "confirm:coupon", "confirm:no"}
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] == "confirm"


async def test_the_booking_summary_explains_the_distance_charge_with_the_free_km():
    line = WhatsAppBotService._bill_extras({"travel": {"charge": 19, "distance_km": 12.75, "free_km": 5.0, "per_km_rate": 2.5}})
    assert "Distance charge (12.8 km · first 5 km free): ₹19" in line or "Distance charge (12.75 km · first 5 km free): ₹19" in line
    assert "Distance" not in WhatsAppBotService._bill_extras({"travel": {"charge": 0, "distance_km": 3.0, "free_km": 5.0}})


async def test_a_car_price_flow_has_no_bike_add_on_and_never_mentions_a_distance_charge(db, clean):
    s = await _after_hi(db, clean)
    await say(s, text="price")
    await say(s, reply=f"pt:{s['hatch']}")
    listing = await last(db, s)
    assert f"ps:{s['hatch']}:addons" in row_ids(listing)
    await say(s, reply=f"ps:{s['hatch']}:addons")
    addons = await last(db, s)
    assert "Extra Bike Wash" not in addons["message"] and addons["message"].startswith("*Add-Ons*")
    charging = await db.services.find({"is_active": True, "charges_travel": True, "is_addon": {"$ne": True}}).to_list(None)
    for svc in charging:
        if svc.get("vehicle_types") and s["hatch"] not in svc["vehicle_types"]:
            continue
        await say(s, reply=f"ps:{s['hatch']}:{svc['_id']}")
        assert "distance" not in (await last(db, s))["message"].lower()  # never in a price answer
    await say(s, text="bike ka rate")
    bike = await last(db, s)
    bike_type = next(t for t in await _types(db) if "bike" in t["name"].lower())
    assert bike["interactive_kind"] == "list" and all(r.startswith(f"ps:{bike_type['_id']}:") for r in row_ids(bike))


async def test_price_taps_never_disturb_a_booking_in_progress(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    before = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    await say(s, reply=f"ps:{s['hatch']}:{s['star']}")  # an old price list tapped mid-booking
    after = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert after["state"] == before["state"] == "q_service" and after["data"] == before["data"]
    await say(s, reply=f"pbook:{s['hatch']}:{s['star']}")  # Book Now mid-booking never wipes the draft
    again = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert again["state"] == "q_service" and again["data"]["cur"] == before["data"]["cur"]
    assert "already booking" in (await outbox(db, s))[-2]["message"]


async def test_a_stale_price_row_for_a_removed_type_is_handled_politely(db, clean):
    s = await _after_hi(db, clean)
    await say(s, reply=f"pt:{ObjectId()}")
    sent = await outbox(db, s)
    assert "isn't available any more" in sent[-2]["message"] and sent[-1]["interactive_kind"] == "list"


async def test_a_five_digit_pincode_is_refused_with_help_instead_of_crashing_the_address(db, clean):
    s = await _rig(db, clean)
    await db.whatsapp_conversations.update_one(
        {"wa_id": s["wa_id"]}, {"$set": {"state": "await_addr_pincode", "data": {"latitude": 1.0, "longitude": 1.0}, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}},
        upsert=True,
    )
    await say(s, text="45201")
    assert "6-digit" in (await last(db, s))["message"]
    assert (await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]}))["state"] == "await_addr_pincode"
