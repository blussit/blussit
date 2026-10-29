"""Use My Plan — only for the right car type and wash, and paid add-ons on a
plan-covered wash (2026-10-29): the table of vehicle type x wash x pass state
at the quote and at a forged create, the plan note, the guest rule, the
quote/booking breakdown and the on-site add-on on a plan booking.
Local Mongo only."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


async def _types(db) -> dict:
    return {t["slug"]: str(t["_id"]) for t in await db.vehicle_types.find({}).to_list(None)}


async def _svc(db, slug) -> dict:
    return await db.services.find_one({"slug": slug})


async def _setup(db):
    """A customer with ONE type pass: Hatchback + Star Wash (4 washes)."""
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    star = str((await _svc(db, "star-wash"))["_id"])
    sub = await pf.std_pass(db, cu, star, vehicle_type=(await _types(db))["hatchback"], washes=4)
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    return s, sub, customer


async def _quote(db, customer, vehicle_type, service_slugs, *, extra: dict | None = None, **kw):
    ids = [str((await _svc(db, slug))["_id"]) for slug in service_slugs]
    return await BookingService(db).quote_visit(
        customer_id=str(customer["_id"]), phone=customer["phone"], scheduled_date=h.day(2),
        lines=[QuickBookingLine(vehicle_type=vehicle_type, service_ids=ids, **(extra or {}))], **kw,
    )


# ----------------------------------------------------- the rule, as a table

# (car type, wash, pass state) -> is the plan used?
CASES = [
    ("hatchback", "star-wash", "active", True),
    ("sedan", "star-wash", "active", False),        # another car type
    ("suv", "star-wash", "active", False),
    ("bike", "bike-wash", "active", False),          # a car plan never pays for a bike
    ("hatchback", "deep-cleaning", "active", False),  # another wash
    ("hatchback", "waterless-service", "active", False),
    ("hatchback", "star-wash", "used_up", False),
    ("hatchback", "star-wash", "ended", False),
    ("hatchback", "star-wash", "cancelled", False),
    ("hatchback", "star-wash", "scheduled", False),
]


@pytest.mark.parametrize("car,wash,state,used", CASES, ids=[f"{c}-{w}-{st}" for c, w, st, _ in CASES])
async def test_quote_uses_the_plan_only_for_the_right_car_and_wash(db, car, wash, state, used):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    sets = {
        "active": {},
        "used_up": {"remaining_service_count": 0, "status": "expired"},
        "ended": {"end_date": now_ist() - timedelta(days=2)},
        "cancelled": {"status": "cancelled"},
        "scheduled": {"status": "scheduled", "start_date": now_ist() + timedelta(days=6)},
    }[state]
    if sets:
        await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$set": sets})
    quote = await _quote(db, customer, types[car], [wash])
    row = quote["lines"][0]
    assert (row["plan_covered"] == 1) is used, quote
    if used:
        assert quote["plan_discount"] > 0 and row["plan_note"] is None
        assert [c["name"] for c in row["plan_covers"]] == ["Star Wash"] and row["plan_extras"] == []
    else:
        assert quote["plan_discount"] == 0 and row["subscription_ids"] == []
    if state == "active" and not used:
        # The customer is told WHY — what the plan does cover.
        assert row["plan_note"] and "Hatchback Star Wash only" in row["plan_note"] and "normal price" in row["plan_note"]
    if state != "active":
        assert row["plan_note"] is None  # nothing usable to explain


async def test_pay_normal_price_choice_gets_no_note_and_keeps_the_wash(db):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    quote = await _quote(db, customer, types["hatchback"], ["star-wash"], extra={"use_subscription": False})
    assert quote["lines"][0]["plan_covered"] == 0 and quote["lines"][0]["plan_note"] is None
    assert quote["total_amount"] == 349


async def test_a_quote_never_names_plans_to_a_guest(db):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    star = str((await _svc(db, "star-wash"))["_id"])
    quote = await BookingService(db).quote_visit(
        customer_id=None, phone=None, anonymous=True, lines=[QuickBookingLine(vehicle_type=types["sedan"], service_ids=[star])],
    )
    assert quote["lines"][0]["plan_note"] is None and quote["lines"][0]["plan_covered"] == 0


# ---------------------------------------------- a forged request is refused


@pytest.mark.parametrize("car,wash,needle", [
    ("sedan", "star-wash", "different vehicle type"),
    ("hatchback", "deep-cleaning", "covers Hatchback Star Wash only"),
])
async def test_a_forged_plan_choice_is_refused_in_words(db, car, wash, needle):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    with pytest.raises(BadRequestException, match=needle):
        await _quote(db, customer, types[car], [wash], extra={"subscription_id": sub})


async def test_a_car_plan_can_never_pay_for_a_bike(db):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    star = await _svc(db, "star-wash")
    # Even on a service listed for both classes, the class check holds.
    with pytest.raises(BadRequestException, match="different vehicle type"):
        await UserSubscriptionService(db).plan_consumption(sub, None, [star], str(customer["_id"]), vehicle_type=types["bike"])


async def test_a_bigger_tier_plan_still_washes_a_smaller_car_but_never_the_reverse(db):
    """The documented tier rule, unchanged: bought for a sedan it also covers a hatchback."""
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    types = await _types(db)
    star_id = str((await _svc(db, "star-wash"))["_id"])
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    sub = await pf.std_pass(db, cu, star_id, vehicle_type=types["sedan"], washes=2)
    star = await _svc(db, "star-wash")
    got = await UserSubscriptionService(db).plan_consumption(sub, None, [star], cu, vehicle_type=types["hatchback"])
    assert got == {"flat_count": 1}
    with pytest.raises(BadRequestException, match="different vehicle type"):
        await UserSubscriptionService(db).plan_consumption(sub, None, [star], cu, vehicle_type=types["suv"])
    del customer


async def test_a_retyped_car_cannot_use_its_old_plan(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    types = await _types(db)
    star_id = str((await _svc(db, "star-wash"))["_id"])
    sub = await pf.std_pass(db, cu, star_id, car_id=s["cu"]["vehicle_id"], washes=3)
    await db.vehicles.update_one({"_id": ObjectId(s["cu"]["vehicle_id"])}, {"$set": {"vehicle_type": types["sedan"]}})
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    with pytest.raises(BadRequestException, match="different vehicle type"):
        await _quote(db, customer, types["sedan"], ["star-wash"], extra={"vehicle_id": s["cu"]["vehicle_id"], "subscription_id": sub})


# ------------------------------- plan wash + paid add-ons: one booking, one bill


async def test_quote_splits_the_plan_part_from_the_paid_addons(db):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    quote = await _quote(db, customer, types["hatchback"], ["star-wash", "exterior-polish"])
    row = quote["lines"][0]
    assert row["plan_covered"] == 1 and [c["name"] for c in row["plan_covers"]] == ["Star Wash"]
    assert [(e["name"], e["amount"]) for e in row["plan_extras"]] == [("Exterior Polish", 200.0)]
    assert row["plan_extras_total"] == 200 and quote["plan_extras_total"] == 200 and quote["plan_covered_total"] == 349
    assert quote["total_amount"] == 200  # the customer pays the add-on only
    assert quote["online_only"] is True  # the website: extras on a plan wash are paid before we come


async def test_a_plan_wash_with_addons_books_charges_only_the_addon_and_uses_one_wash(db):
    s, sub, customer = await _setup(db)
    cu = s["cu"]
    types = await _types(db)
    ids = [str((await _svc(db, slug))["_id"]) for slug in ("star-wash", "exterior-polish")]
    slot = s["keys"][0]
    out = await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Plan Person", customer_phone=customer["phone"], address_id=cu["address_id"],
            lines=[QuickBookingLine(vehicle_type=types["hatchback"], service_ids=ids)],
            scheduled_date=s["when"], scheduled_slot=slot, payment_method="cash",
        ),
        customer=customer, source="staff", allow_pinless=True,
    )
    booking = await fb.doc(db, out["bookings"][0]["id"])
    assert booking["subscription_id"] == sub and booking["total_amount"] == 200 and booking["discount_amount"] == 349
    assert await fb.remaining(db, sub) == 3
    view = out["bookings"][0]["plan_split"]
    assert view["covered_amount"] == 349 and view["extras_total"] == 200
    assert [c["name"] for c in view["covers"]] == ["Star Wash"] and [e["name"] for e in view["extras_booked"]] == ["Exterior Polish"]
    # The confirmation names both.
    note = await db.notifications.find_one({"user_id": cu["id"], "title": "Booking confirmed"})
    assert note and "Your plan covers the wash" in note["message"] and "add-ons ₹200 are paid extra" in note["message"]


async def test_api_quote_and_create_agree_on_a_plan_wash_with_addons(db):
    s, sub, customer = await _setup(db)
    cu = s["cu"]
    types = await _types(db)
    ids = [str((await _svc(db, slug))["_id"]) for slug in ("star-wash", "exterior-polish")]
    lines = [{"vehicle_type": types["hatchback"], "quantity": 1, "service_ids": ids}]
    async with h.client() as c:
        q = await c.post("/api/v1/bookings/quote", json={"lines": lines, "address_id": cu["address_id"], "scheduled_date": s["when"]}, headers=cu["h"])
        assert q.status_code == 200, q.text
        quote = q.json()["data"]
        assert quote["plan_extras_total"] == 200 and quote["lines"][0]["plan_extras"][0]["name"] == "Exterior Polish"
        r = await c.post("/api/v1/bookings/quick", json={
            "customer_name": "Plan Person", "customer_phone": customer["phone"], "address_id": cu["address_id"], "lines": lines,
            "scheduled_date": s["when"], "scheduled_slot": s["keys"][0], "payment_method": "online", "expected_total": quote["total_amount"],
        }, headers=cu["h"])
        assert r.status_code == 200, r.text
    created = r.json()["data"]
    assert created["total_amount"] == quote["total_amount"] == 200
    doc = await fb.doc(db, created["bookings"][0]["id"])
    assert doc["subscription_id"] == sub and doc["status"] == "awaiting_payment" and doc["amount_due"] == 200


# ----------------------------------------------------------------- guests


async def test_a_guest_booking_never_spends_a_plan_the_number_happens_to_own(db):
    from app.controllers.booking_controller import BookingController
    from app.schemas.booking_schema import QuickAddress

    s, sub, customer = await _setup(db)
    cu = s["cu"]
    types = await _types(db)
    star = str((await _svc(db, "star-wash"))["_id"])
    code, now = "246810", datetime.now(timezone.utc)
    await db.otp_requests.insert_one({
        "_id": f"otp:{customer['phone']}", "identifier": customer["phone"], "otp": code, "purpose": "booking_confirmation",
        "expires_at": now + timedelta(minutes=10), "last_sent_at": now, "attempts": 0, "verified": False,
    })
    address = await db.addresses.find_one({"_id": ObjectId(cu["address_id"])})
    out = (await BookingController(db).quick_create(None, QuickBookingRequest(
        customer_name="Guest", customer_phone=customer["phone"], phone_otp=code,
        address=QuickAddress(line1=address["line1"], pincode=address["pincode"], latitude=address.get("latitude"), longitude=address.get("longitude")),
        lines=[QuickBookingLine(vehicle_type=types["hatchback"], service_ids=[star])],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    )))["data"]
    doc = await fb.doc(db, out["bookings"][0]["id"])
    assert not doc.get("subscription_id") and doc["total_amount"] > 0
    assert await fb.remaining(db, sub) == 4  # the wash is still there for a logged-in booking


# ----------------------------------------- on site: the captain adds an add-on


async def _plan_booking_on_site(db):
    s, sub, customer = await _setup(db)
    cu = s["cu"]
    types = await _types(db)
    star = str((await _svc(db, "star-wash"))["_id"])
    out = await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Plan Person", customer_phone=customer["phone"], address_id=cu["address_id"],
            lines=[QuickBookingLine(vehicle_type=types["hatchback"], service_ids=[star])],
            scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
        ),
        customer=customer, source="staff", allow_pinless=True,
    )
    bid = out["bookings"][0]["id"]
    await fb.assign(db, bid, s)
    await fb.hb.on_the_way(db, bid, s["cap"]["id"], vehicle_verified=True)
    return s, sub, bid


async def test_captain_adds_an_addon_to_a_plan_wash_priced_by_the_system(db):
    s, sub, bid = await _plan_booking_on_site(db)
    polish = await _svc(db, "exterior-polish")
    before = await fb.doc(db, bid)
    assert before["total_amount"] == 0 and before["payment_status"] == "paid"
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=s["cap"]["h"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["added_total"] == 200 and data["amount_due"] == 200  # the catalogue price, not anything typed
    after = await fb.doc(db, bid)
    assert after["total_amount"] == 200 and after["subscription_id"] == sub and after["captain_earning"] == before["captain_earning"]
    assert after["platform_earning"] == before["platform_earning"] + 200
    split = data["booking"]["plan_split"]
    assert split["extras_on_site_total"] == 200 and split["extras_booked_total"] == 0 and split["extras_total"] == 200
    assert [e["name"] for e in split["extras_on_site"]] == ["Exterior Polish"] and split["covered_amount"] == 349
    # The customer is told in the app AND on WhatsApp, in words.
    cu_id = s["cu"]["id"]
    note = await db.notifications.find_one({"user_id": cu_id, "title": "Add-On Added To Your Booking"})
    assert note and "Exterior Polish" in note["message"] and "₹200 extra" in note["message"] and "Your plan still covers Star Wash" in note["message"]
    assert await db.whatsapp_queue.find_one({"user_id": cu_id, "title": "Add-On Added To Your Booking"})
    # The manager sees it in the booking timeline.
    history = await db.booking_status_history.find({"booking_id": bid}).to_list(None)
    assert any("Exterior Polish" in (row.get("note") or "") and "₹200" in (row.get("note") or "") for row in history)
    # The captain's cash for it settles the usual way: the platform's share is debited.
    from app.services.money_service import MoneyService

    await MoneyService(db).apply_payment([bid], 200, method="cash", key="plan-addon-cash")
    paid = await fb.doc(db, bid)
    assert paid["payment_status"] == "paid" and paid["amount_due"] == 0 and paid["paid_cash"] == 200


async def test_an_addon_that_is_not_offered_for_the_car_type_is_refused(db):
    s, sub, bid = await _plan_booking_on_site(db)
    bike_polish = await _svc(db, "bike-polish")
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{bid}/add-services", json={"service_ids": [str(bike_polish["_id"])]}, headers=s["cap"]["h"])
    assert r.status_code == 400


# --------------------------------------------------------- reports (revenue)


async def test_plan_addon_money_is_paid_revenue_not_plan_revenue(db):
    """A plan booking's covered wash is "Subscription-Covered" (its waiver); the
    add-on it charged is paid revenue ("Plan Add-Ons (Paid)"), inside the total."""
    from app.services.kpi_service import KpiService, resolve_period

    s = await fb.rig(db)
    cu = s["cu"]["id"]
    day = "2018-05-09"
    start, end, prev_start, prev_end = resolve_period(None, day, day)
    at = start + timedelta(hours=10)
    res = await db.bookings.insert_many([{
        "customer_id": cu, "vehicle_id": s["cu"]["vehicle_id"], "service_center_id": s["center_id"], "service_ids": [],
        "status": "completed", "scheduled_date": at.replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
        "created_at": at, "updated_at": at, "closed_at": at, "booking_number": f"BKPLANREV{next(_seq)}",
        "payment_method": "subscription", "subtotal": 549.0, "discount_amount": 349.0, "total_amount": 200.0,
    }])
    try:
        q = (await KpiService(db).business(start, end, prev_start, prev_end))["revenue_quality"]
        assert q["total"] == 200.0 and q["subscription_revenue"] == 349.0 and q["plan_addon_revenue"] == 200.0
    finally:
        await db.bookings.delete_many({"_id": {"$in": res.inserted_ids}})


# ------------------------------------------- forged pass states / double use


@pytest.mark.parametrize("state", ["used_up", "ended", "cancelled", "scheduled"])
async def test_a_forged_request_naming_an_unusable_pass_is_refused(db, state):
    s, sub, customer = await _setup(db)
    types = await _types(db)
    sets = {
        "used_up": {"remaining_service_count": 0, "status": "expired"},
        "ended": {"end_date": now_ist() - timedelta(days=2), "status": "expired"},
        "cancelled": {"status": "cancelled"},
        "scheduled": {"status": "scheduled", "start_date": now_ist() + timedelta(days=6)},
    }[state]
    await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$set": sets})
    with pytest.raises(BadRequestException):
        await _quote(db, customer, types["hatchback"], ["star-wash"], extra={"subscription_id": sub})


async def test_one_wash_pays_for_one_car_only_even_when_two_lines_name_it(db):
    s = await fb.rig(db)
    cu = s["cu"]
    types = await _types(db)
    star = str((await _svc(db, "star-wash"))["_id"])
    sub = await pf.std_pass(db, cu["id"], star, vehicle_type=types["hatchback"], washes=1)
    customer = await db.users.find_one({"_id": ObjectId(cu["id"])})
    lines = [QuickBookingLine(vehicle_type=types["hatchback"], service_ids=[star], subscription_id=sub) for _ in range(2)]
    with pytest.raises(BadRequestException):
        await BookingService(db).create_quick_booking(
            QuickBookingRequest(
                customer_name="Plan Person", customer_phone=customer["phone"], address_id=cu["address_id"], lines=lines,
                scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
            ),
            customer=customer, source="staff", allow_pinless=True,
        )
    assert await fb.remaining(db, sub) == 1  # nothing was spent
    # All-or-nothing: the first car was rolled back (cancelled, its wash returned), nothing is live.
    assert await db.bookings.count_documents({"customer_id": cu["id"], "status": {"$ne": "cancelled"}}) == 0
