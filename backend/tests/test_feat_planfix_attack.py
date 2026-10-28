"""PLANS-AUDIT 2026-10-28 — attacker's pass over bookings x plans x services.
Each test is one scenario; a hole found here gets a fix, then stays as a
regression. Local Mongo only."""
import asyncio
from datetime import timedelta

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.schemas.booking_schema import BookingCancelRequest, QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests import test_fix_core_helpers as h
from tests.test_feat_planfix_helpers import set_dates

pytestmark = pytest.mark.asyncio


def _req(s, lines, slot, when=None, method="cash", coupon=None) -> QuickBookingRequest:
    return QuickBookingRequest(
        customer_name="Attack", customer_phone="9876543212", address_id=s["cu"]["address_id"], lines=lines,
        scheduled_date=when or s["when"], scheduled_slot=slot, payment_method=method, coupon_code=coupon,
    )


async def _rig(db, washes=4):
    s = await fb.rig(db)
    sv = await pf.services(db)
    hatch = await fb.get_hatchback_type_id(db)
    cu = s["cu"]["id"]
    sub = await pf.std_pass(db, cu, sv["star-wash"], car_id=s["cu"]["vehicle_id"], washes=washes)
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    return s, sv, hatch, sub, customer


async def test_another_customers_pass_or_car_in_a_line_is_a_404(db):
    s, sv, hatch, sub, customer = await _rig(db)
    stranger = await h.customer(db, s["pin"])
    svc = BookingService(db)
    # Stranger's pass id on my booking.
    theirs = await pf.std_pass(db, stranger["id"], sv["star-wash"], car_id=stranger["vehicle_id"])
    line = QuickBookingLine(vehicle_type=hatch, vehicle_id=s["cu"]["vehicle_id"], subscription_id=theirs, service_ids=[sv["star-wash"]])
    with pytest.raises(NotFoundException):
        await svc.create_quick_booking(_req(s, [line], s["keys"][0]), customer=customer, source="app")
    assert (await pf.sub_doc(db, theirs))["remaining_service_count"] == 4
    # Stranger's car on my booking.
    line = QuickBookingLine(vehicle_type=hatch, vehicle_id=stranger["vehicle_id"], subscription_id=sub, service_ids=[sv["star-wash"]])
    with pytest.raises(NotFoundException):
        await svc.create_quick_booking(_req(s, [line], s["keys"][0]), customer=customer, source="app")


async def test_a_pass_never_covers_a_bigger_car_type(db):
    s, sv, hatch, sub, customer = await _rig(db)
    cu = s["cu"]["id"]
    suv = await fb.get_suv_type_id(db)
    suv_car = await pf.extra_car(db, cu, suv)
    type_pass = await pf.std_pass(db, cu, sv["waterless-service"], vehicle_type=hatch)
    svc = BookingService(db)
    # SUV booked with the hatchback type pass named explicitly.
    line = QuickBookingLine(vehicle_type=suv, vehicle_id=suv_car, subscription_id=type_pass, service_ids=[sv["waterless-service"]])
    with pytest.raises(BadRequestException):
        await svc.create_quick_booking(_req(s, [line], s["keys"][0]), customer=customer, source="app")
    # SUV type line, no car: the hatchback pass is not applied (charged normally).
    out = await svc.create_quick_booking(_req(s, [QuickBookingLine(vehicle_type=suv, service_ids=[sv["waterless-service"]])], s["keys"][1]), customer=customer, source="app")
    assert (await fb.doc(db, out["bookings"][0]["id"]))["subscription_id"] is None


async def test_add_ons_stay_paid_on_a_covered_wash_and_a_coupon_cannot_stack(db):
    s, sv, hatch, sub, customer = await _rig(db)
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"], sv["exterior-polish"]])
    quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=customer["phone"], lines=[line], scheduled_date=s["when"], coupon_code="FREEBIKE")
    polish = await db.services.find_one({"slug": "exterior-polish"})
    assert quote["total_amount"] == svc._resolve_price(polish, hatch, False)
    assert quote.get("coupon_error") or not quote.get("coupon_code")


async def test_quantity_tricks_are_refused_or_neutral(db):
    s, sv, hatch, sub, customer = await _rig(db)
    svc = BookingService(db)
    for bad in (-1, 0, 10**9):
        line = QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"]], service_quantities={sv["star-wash"]: bad})
        try:
            quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=customer["phone"], lines=[line], scheduled_date=s["when"])
        except AppException:
            continue
        # If accepted it must still never be a negative or a free multi-wash.
        assert quote["total_amount"] >= 0 and quote["lines"][0]["plan_covered"] <= 1
    with pytest.raises(ValidationError):
        QuickBookingLine(vehicle_type=hatch, quantity=1.5, service_ids=[sv["star-wash"]])
    with pytest.raises(ValidationError):
        QuickBookingLine(vehicle_type=hatch, quantity=11, service_ids=[sv["star-wash"]])
    with pytest.raises(ValidationError):
        QuickBookingLine(vehicle_type=hatch, quantity=-2, service_ids=[sv["star-wash"]])


async def test_last_wash_cannot_be_spent_twice_in_a_race(db):
    s, sv, hatch, sub, customer = await _rig(db, washes=1)
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"]])

    async def go(i):
        try:
            return await BookingService(db).create_quick_booking(_req(s, [line], s["keys"][i]), customer=customer, source="app")
        except AppException as exc:
            return exc

    res = await asyncio.gather(go(0), go(1), go(2))
    made = [r for r in res if isinstance(r, dict)]
    covered = [r for r in made if (await fb.doc(db, r["bookings"][0]["id"]))["subscription_id"]]
    left = (await pf.sub_doc(db, sub))["remaining_service_count"]
    assert left == 0 and len(covered) == 1, (left, len(covered))
    # Everything that got no wash was charged, never free.
    for r in made:
        d = await fb.doc(db, r["bookings"][0]["id"])
        if not d["subscription_id"]:
            assert d["total_amount"] > 0


async def test_cancel_and_rebook_farms_nothing(db):
    s, sv, hatch, sub, customer = await _rig(db)
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"]])
    out = await svc.create_quick_booking(_req(s, [line], s["keys"][0]), customer=customer, source="app")
    bid = out["bookings"][0]["id"]
    await fb.slot_in(db, bid, 600)
    await svc.cancel_booking(bid, BookingCancelRequest(reason="Plans changed"), s["cu"]["id"], "customer")
    assert (await pf.sub_doc(db, sub))["remaining_service_count"] == 4
    # A second cancel of the same booking must not hand a second wash back.
    with pytest.raises(AppException):
        await svc.cancel_booking(bid, BookingCancelRequest(reason="Plans changed"), s["cu"]["id"], "customer")
    assert (await pf.sub_doc(db, sub))["remaining_service_count"] == 4
    assert await fb.balance(db, s["cu"]["id"]) == 0


async def test_pass_before_start_and_past_end_refused(db):
    s, sv, hatch, sub, customer = await _rig(db)
    svc = BookingService(db)
    far = (await pf.sub_doc(db, sub))["end_date"] + timedelta(days=3)
    explicit = QuickBookingLine(vehicle_type=hatch, vehicle_id=s["cu"]["vehicle_id"], subscription_id=sub, service_ids=[sv["star-wash"]])
    with pytest.raises(AppException):
        await svc.create_quick_booking(_req(s, [explicit], s["keys"][0], when=far.date().isoformat()), customer=customer, source="app")
    # A scheduled (not-yet-started) pass is never spendable.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$set": {"status": "scheduled", "start_date": pf.now_ist() + timedelta(days=10)}})
    with pytest.raises(AppException):
        await svc.create_quick_booking(_req(s, [explicit], s["keys"][0]), customer=customer, source="app")
    assert (await pf.sub_doc(db, sub))["remaining_service_count"] == 4


async def test_deleted_car_cannot_be_used_with_its_pass(db):
    s, sv, hatch, sub, customer = await _rig(db)
    await db.vehicles.update_one({"_id": ObjectId(s["cu"]["vehicle_id"])}, {"$set": {"is_deleted": True}})
    svc = BookingService(db)
    explicit = QuickBookingLine(vehicle_type=hatch, vehicle_id=s["cu"]["vehicle_id"], subscription_id=sub, service_ids=[sv["star-wash"]])
    with pytest.raises(AppException):
        await svc.create_quick_booking(_req(s, [explicit], s["keys"][0]), customer=customer, source="app")
    assert await svc._usable_passes(s["cu"]["id"]) == []
    assert (await pf.sub_doc(db, sub))["remaining_service_count"] == 4


async def test_REQUEST_vehicle_delete_during_a_pass_extension(db):
    """A car whose pass is inside its extension (Last Booking Day) cannot be
    removed — that would strand the pass."""
    from app.services.profile_service import VehicleService

    s, sv, hatch, sub, customer = await _rig(db)
    await set_dates(db, sub, end=timedelta(hours=-5), extended=timedelta(days=3))
    with pytest.raises(AppException):
        await VehicleService(db).delete(s["cu"]["id"], s["cu"]["vehicle_id"])
