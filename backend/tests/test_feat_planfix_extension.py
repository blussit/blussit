"""PLANS-AUDIT 2026-10-28 — an EXTENDED pass must work on every booking path.
Symptom (founder): "even when a plan is extended we can't add a booking from
that plan". Root cause: `_usable_passes` (the auto-apply lookup behind the
website, app, bot, manager-quick, quote and Log A Done Job) compared only
`end_date`, never `extended_until` / Last Booking Day. Local Mongo only."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import (
    UserSubscriptionService,
    find_subscriptions_ended,
    mark_subscription_expired,
)
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests.test_feat_planfix_helpers import set_dates

pytestmark = pytest.mark.asyncio


def _req(s, line, slot, when=None) -> QuickBookingRequest:
    return QuickBookingRequest(
        customer_name="Plan Fix", customer_phone="9876543211", address_id=s["cu"]["address_id"], lines=[line],
        scheduled_date=when or s["when"], scheduled_slot=slot, payment_method="cash",
    )


async def _rig(db):
    s = await fb.rig(db)
    sv = await pf.services(db)
    star = sv["star-wash"]
    hatch = await fb.get_hatchback_type_id(db)
    # A car-bound pass that ended 12 h ago and was then extended 5 days.
    sub_id = await pf.std_pass(db, s["cu"]["id"], star, car_id=s["cu"]["vehicle_id"])
    await set_dates(db, sub_id, end=timedelta(hours=-12), extended=timedelta(days=5), start=timedelta(days=-31))
    customer = await db.users.find_one({"_id": ObjectId(s["cu"]["id"])})
    return s, sv, star, hatch, sub_id, customer


async def test_usable_passes_lists_a_pass_inside_its_extension(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    got = await BookingService(db)._usable_passes(s["cu"]["id"])
    assert [str(p["_id"]) for p in got] == [sub_id]


async def test_type_line_quote_and_create_use_the_extended_pass(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=hatch, service_ids=[star])
    quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=customer["phone"], lines=[line], scheduled_date=s["when"])
    assert quote["total_amount"] == 0
    out = await svc.create_quick_booking(_req(s, line, s["keys"][0]), customer=customer, source="app")
    d = await fb.doc(db, out["bookings"][0]["id"])
    assert d["subscription_id"] == sub_id and d["vehicle_id"] == s["cu"]["vehicle_id"] and d["total_amount"] == 0
    assert (await pf.sub_doc(db, sub_id))["remaining_service_count"] == 3


async def test_old_create_paths_use_the_extended_pass(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    svc = BookingService(db)
    from app.schemas.booking_schema import BookingCreateRequest

    # POST /bookings (type-only): the pass is matched and stamped.
    out = await svc.create_self_service_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=hatch, address_id=s["cu"]["address_id"], service_ids=[star], scheduled_date=s["when"],
        scheduled_slot=s["keys"][0], payment_method="cash"))
    assert (await fb.doc(db, out["id"]))["subscription_id"] == sub_id
    # Explicit (the customer app's "Book A Plan Wash" sheet).
    out2 = await svc.create_self_service_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_id=s["cu"]["vehicle_id"], address_id=s["cu"]["address_id"], service_ids=[star], scheduled_date=s["when"],
        scheduled_slot=s["keys"][1], payment_method="cash", subscription_id=sub_id))
    assert (await fb.doc(db, out2["id"]))["subscription_id"] == sub_id
    assert (await pf.sub_doc(db, sub_id))["remaining_service_count"] == 2


async def test_manager_log_uses_the_extended_pass(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    from app.utils.timezone import now_ist

    when = (now_ist() - timedelta(days=1)).date().isoformat()  # inside the old period, before the pass "ended"
    log = ManagerLogBookingRequest(
        customer_name="Plan Fix", customer_phone=customer["phone"], address_id=s["cu"]["address_id"], scheduled_date=when,
        service_time="10:00", lines=[QuickBookingLine(vehicle_type=hatch, service_ids=[star], use_subscription=True)],
    )
    out = await BookingService(db).create_manager_logged_visit(log, manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    d = await fb.doc(db, out["bookings"][0]["id"])
    assert d["subscription_id"] == sub_id


async def test_sweeps_leave_an_extended_pass_and_its_washes_alone(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    assert sub_id not in [str(x["_id"]) for x in await find_subscriptions_ended(db)]
    await mark_subscription_expired(db, sub_id)
    assert (await pf.sub_doc(db, sub_id))["status"] == "active"
    # After the extension is over the sweep does expire it.
    await set_dates(db, sub_id, end=timedelta(days=-8), extended=timedelta(days=-1))
    assert sub_id in [str(x["_id"]) for x in await find_subscriptions_ended(db)]


async def test_reschedule_and_edit_inside_and_beyond_the_extension(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=hatch, service_ids=[star])
    out = await svc.create_quick_booking(_req(s, line, s["keys"][0]), customer=customer, source="app")
    bid = out["bookings"][0]["id"]
    from app.core.exceptions import BadRequestException
    from app.schemas.booking_schema import BookingRescheduleRequest

    far = (await pf.sub_doc(db, sub_id))["extended_until"]
    beyond = (far + timedelta(days=2)).date().isoformat()
    with pytest.raises(BadRequestException):
        await svc.reschedule_booking(bid, BookingRescheduleRequest(scheduled_date=beyond, scheduled_slot=s["keys"][0]), s["cu"]["id"], "customer")


async def test_a_pass_past_its_extension_is_not_offered(db):
    s, sv, star, hatch, sub_id, customer = await _rig(db)
    await set_dates(db, sub_id, end=timedelta(days=-8), extended=timedelta(days=-1))
    assert await BookingService(db)._usable_passes(s["cu"]["id"]) == []
    view = next(p for p in await UserSubscriptionService(db).list_my_subscriptions(s["cu"]["id"]) if p["id"] == sub_id)
    assert view["effective_status"] == "expired"
