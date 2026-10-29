"""WhatsApp: "Use My Plan" only for the right car type and wash, and "Want To
Add Extras?" after it — add-ons from the live catalogue for the car's type,
paid on top of a plan-covered wash (2026-10-29). Local Mongo only."""
import pytest
from bson import ObjectId

from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_feat_planfix_helpers as pf
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture
from tests.test_whatsapp_bot_alignment import _rig, last, outbox, row_ids, say, to_when

pytestmark = pytest.mark.asyncio


async def _plan_rig(db, clean, washes=4):
    s = await _rig(db, clean)
    sub = await pf.std_pass(db, s["cu"]["id"], s["star"], car_id=s["cu"]["vehicle_id"], washes=washes)
    s["polish"] = await db.services.find_one({"slug": "exterior-polish"})
    return s, sub


async def _to_extras_ask(db, s, sub) -> dict:
    await to_when(db, s)
    await say(s, reply=f"plan:{sub}")
    return await last(db, s)


async def _finish(db, s) -> dict:
    await say(s, reply=f"when:{hh.day(2)}|09:00-12:00")
    await say(s, reply=f"addr:{s['cu']['address_id']}")
    summary = await last(db, s)
    await say(s, reply="confirm:yes")
    booking = await db.bookings.find_one({"customer_id": s["cu"]["id"]})
    return {"summary": summary, "booking": booking}


async def test_using_the_plan_asks_about_extras_with_two_buttons(db, clean):
    s, sub = await _plan_rig(db, clean)
    msg = await _to_extras_ask(db, s, sub)
    assert msg["interactive_kind"] == "buttons" and "Want To Add Extras" in msg["message"]
    assert {b["id"]: b["title"] for b in msg["options"]} == {"ext:yes": "Yes, Add Extras", "ext:no": "No, Continue"}
    assert all(len(b["title"]) <= 20 for b in msg["options"])


async def test_plan_with_one_extra_charges_only_the_extra_and_uses_one_wash(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, reply="ext:yes")
    lst = await last(db, s)
    assert lst["interactive_kind"] == "list"
    ids = row_ids(lst)
    assert f"ext:{s['polish']['_id']}" in ids and "ext:done" in ids
    assert not any("Extra Bike Wash" in (r.get("title") or "") for r in lst["options"])  # the bike counter is never a pick
    for r in lst["options"]:  # Meta limits
        assert len(r["title"]) <= 24 and len(r.get("description") or "") <= 72
    polish_row = next(r for r in lst["options"] if r["id"] == f"ext:{s['polish']['_id']}")
    assert "₹200" in polish_row["description"]
    await say(s, reply=f"ext:{s['polish']['_id']}")  # the only extra for a car: straight on to the day
    sent = await outbox(db, s)
    assert any("Added" in m["message"] and "₹200" in m["message"] for m in sent)
    assert (await last(db, s))["interactive_kind"] == "list" and any(i.startswith("when:") for i in row_ids(await last(db, s)))
    out = await _finish(db, s)
    summary = out["summary"]["message"]
    assert "Covered by plan" in summary and "Add-ons (paid): ₹200" in summary and "Your plan covers" in summary
    assert "➕ Exterior Polish" in summary and summary.rstrip().endswith("💰 ₹200")
    booking = out["booking"]
    assert booking["subscription_id"] == sub and booking["total_amount"] == 200 and booking["source"] == "whatsapp"
    assert str(s["polish"]["_id"]) in booking["service_ids"]
    assert await fb.remaining(db, sub) == 3
    confirmed = next(m for m in await outbox(db, s) if "Booking confirmed" in m["message"])
    assert "Covered by your plan" in confirmed["message"] and "3 washes left" in confirmed["message"]
    assert "Add-ons (paid): ₹200" in confirmed["message"] and "You pay: ₹200" in confirmed["message"]
    pay = await last(db, s)  # the same cash-or-online question as any booking
    assert {b["title"] for b in pay["options"]} == {"💳 Pay Online Now", "💵 Cash After Wash"}
    # What the booking screens read: the plan's part and the paid extra, apart.
    from app.services.booking_service import BookingService

    view = (await BookingService(db)._enrich_bookings([booking]))[0]["plan_split"]
    assert [c["name"] for c in view["covers"]] == ["Star Wash"] and view["extras_booked_total"] == 200 and view["extras_total"] == 200
    assert view["covered_amount"] == 349


async def test_typed_yes_then_the_extras_name_then_done(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, text="yes")
    assert (await last(db, s))["interactive_kind"] == "list"
    await say(s, text="polish")
    assert any("Added" in m["message"] for m in await outbox(db, s))
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    line = convo["data"]["lines"][0]
    assert str(s["polish"]["_id"]) in line["service_ids"] and line["extras"][0]["amount"] == 200


async def test_no_extras_books_the_wash_for_nothing(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, text="no")
    out = await _finish(db, s)
    assert out["booking"]["total_amount"] == 0 and out["booking"]["subscription_id"] == sub
    assert "Add-ons" not in out["summary"]["message"]
    assert not any("How would you like to pay?" in m["message"] for m in await outbox(db, s))  # nothing to pay


async def test_done_after_picking_leaves_the_list(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, reply="ext:yes")
    await say(s, reply="ext:done")
    assert any(i.startswith("when:") for i in row_ids(await last(db, s)))
    out = await _finish(db, s)
    assert out["booking"]["total_amount"] == 0


async def test_pay_normal_price_never_asks_about_extras(db, clean):
    s, sub = await _plan_rig(db, clean)
    await to_when(db, s)
    await say(s, reply="plan:none")
    assert not any("Add Extras" in m["message"] for m in await outbox(db, s))
    assert any(i.startswith("when:") for i in row_ids(await last(db, s)))


async def test_a_stale_extra_that_was_switched_off_is_refused_warmly(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, reply="ext:yes")
    await db.services.update_one({"_id": s["polish"]["_id"]}, {"$set": {"is_active": False}})
    try:
        await say(s, reply=f"ext:{s['polish']['_id']}")
        assert any("isn't available any more" in m["message"] for m in await outbox(db, s))
        convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
        assert "extras" not in convo["data"]["lines"][0]
    finally:
        await db.services.update_one({"_id": s["polish"]["_id"]}, {"$set": {"is_active": True}})


async def test_a_plan_for_another_car_type_or_wash_says_what_it_covers(db, clean):
    s, sub = await _plan_rig(db, clean)
    sedan = (await db.vehicle_types.find_one({"slug": "sedan"}))["_id"]
    deep = await db.services.find_one({"slug": "deep-cleaning"})
    # Same wash, a different car type.
    await say(s, text="hi")
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{sedan}")
    await say(s, reply=f"svc:{s['star']}")
    sent = await outbox(db, s)
    note = next(m["message"] for m in sent if "covers" in m["message"])
    assert "Hatchback Star Wash only" in note and "normal price" in note
    assert not any(i.startswith("plan:") for m in sent for i in row_ids(m))
    assert any(i.startswith("when:") for i in row_ids(await last(db, s)))
    # Same car type, a different wash.
    await say(s, text="hi")
    await say(s, reply="menu:book")
    await say(s, reply=f"vt:{s['hatch']}")
    await say(s, reply=f"svc:{deep['_id']}")
    notes = [m["message"] for m in await outbox(db, s) if "covers" in m["message"]]
    assert len(notes) == 2 and "Hatchback Star Wash only" in notes[1]
    assert await fb.remaining(db, sub) == 4  # nothing was spent


async def test_a_side_question_in_the_extras_step_re_offers_it_and_keeps_the_draft(db, clean):
    s, sub = await _plan_rig(db, clean)
    await _to_extras_ask(db, s, sub)
    await say(s, text="kitna rate hai")  # a price question
    again = await last(db, s)
    assert again["interactive_kind"] == "buttons" and {b["id"] for b in again["options"]} == {"ext:yes", "ext:no"}
    await say(s, reply="ext:yes")
    await say(s, text="price list")
    assert (await last(db, s))["interactive_kind"] == "list" and "ext:done" in row_ids(await last(db, s))
    convo = await db.whatsapp_conversations.find_one({"wa_id": s["wa_id"]})
    assert convo["state"] == "q_extras" and convo["data"]["lines"][0]["subscription_id"] == sub
