"""Follow-up 2026-10-07 (HIGH) — a car-bound STANDARD pass (a monthly pass
bought for one car) is auto-applied again on every type-only booking path
(quote, website quick booking, the WhatsApp bot, manager-quick), and the
booking becomes that car. Society and custom passes stay explicit-only
(SOC-4, the custom-plan contract); a line that names a car uses only that
car's own pass; quote == create."""
import pytest
from bson import ObjectId

from app.schemas.booking_schema import QuickBookingLine, QuickBookingRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService
from tests import test_feat_booking_helpers as fb
from tests.factories import make_subscription_plan, make_vehicle

pytestmark = pytest.mark.asyncio


async def _car_pass(db, customer_id: str, vehicle_id: str, washes: int = 4) -> str:
    """A monthly pass bought in the app for ONE car (vehicle_id on the pass)."""
    star = await fb.get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=washes)
    sub = await UserSubscriptionService(db).subscribe(customer_id, SubscribeRequest(plan_id=plan_id, vehicle_id=vehicle_id, service_id=star))
    doc = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert doc["vehicle_id"] == vehicle_id
    return sub["id"]


async def _customer(db, s: dict) -> tuple[dict, str]:
    customer = await db.users.find_one({"_id": ObjectId(s["cu"]["id"])})
    return customer, customer["phone"]


async def _line(db, **kw) -> QuickBookingLine:
    return QuickBookingLine(vehicle_type=await fb.get_hatchback_type_id(db), service_ids=[await fb.get_star_wash_service_id(db)], **kw)


async def _quote_then_book(db, s: dict, lines: list, *, source: str = "app", slot: int = 0) -> tuple[dict, list[dict]]:
    customer, phone = await _customer(db, s)
    svc = BookingService(db)
    address = await db.addresses.find_one({"_id": ObjectId(s["cu"]["address_id"])})
    quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=phone, lines=lines, address=address, source=source,
                                  scheduled_date=s["when"])
    made = await svc.create_quick_booking(
        QuickBookingRequest(customer_name=customer["full_name"], customer_phone=phone, address_id=s["cu"]["address_id"],
                            lines=lines, scheduled_date=s["when"], scheduled_slot=s["keys"][slot]),
        customer=customer, source=source, allow_pinless=True, notify_background=False,
    )
    ids = [b["id"] for b in made["bookings"]] if made.get("bookings") else [made["id"]]
    cars = [await fb.doc(db, i) for i in ids]
    assert quote["total_amount"] == pytest.approx(round(sum(c["total_amount"] for c in cars), 2)), "quote != create"
    return quote, cars


@pytest.mark.parametrize("source", ["app", "whatsapp", "staff"])
async def test_monthly_pass_on_car_a_covers_a_type_only_booking_for_car_a(db, source):
    s = await fb.rig(db)
    car_a = s["cu"]["vehicle_id"]
    sub_id = await _car_pass(db, s["cu"]["id"], car_a)
    _quote, cars = await _quote_then_book(db, s, [await _line(db)], source=source)
    (car,) = cars
    assert car["subscription_id"] == sub_id and car["total_amount"] == 0
    assert car["vehicle_id"] == car_a  # the booking is for that car
    assert await fb.remaining(db, sub_id) == 3


async def test_society_pass_is_never_auto_applied(db):
    s = await fb.rig(db)
    sub_id = await _car_pass(db, s["cu"]["id"], s["cu"]["vehicle_id"])
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"society_id": str(ObjectId()), "plan_kind": "society"}})
    _quote, (car,) = await _quote_then_book(db, s, [await _line(db)])
    assert car["subscription_id"] is None and car["total_amount"] > 0
    assert await fb.remaining(db, sub_id) == 4


async def test_custom_pass_is_never_auto_applied(db):
    s = await fb.rig(db)
    star = await fb.get_star_wash_service_id(db)
    sub_id = await _car_pass(db, s["cu"]["id"], s["cu"]["vehicle_id"])
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {
        "plan_kind": "custom", "total_by_service": {star: 2}, "remaining_by_service": {star: 2},
    }})
    _quote, (car,) = await _quote_then_book(db, s, [await _line(db)])
    assert car["subscription_id"] is None and car["total_amount"] > 0


async def test_a_line_naming_car_b_never_uses_car_a_pass(db):
    s = await fb.rig(db)
    sub_id = await _car_pass(db, s["cu"]["id"], s["cu"]["vehicle_id"])
    car_b = await make_vehicle(db, s["cu"]["id"], await fb.get_hatchback_type_id(db), is_default=False)
    _quote, (car,) = await _quote_then_book(db, s, [await _line(db, vehicle_id=car_b)])
    assert car["vehicle_id"] == car_b and car["subscription_id"] is None and car["total_amount"] > 0
    assert await fb.remaining(db, sub_id) == 4
    # Naming car A itself takes car A's own pass.
    _quote, (own,) = await _quote_then_book(db, s, [await _line(db, vehicle_id=s["cu"]["vehicle_id"])], slot=1)
    assert own["subscription_id"] == sub_id and own["total_amount"] == 0


async def test_two_car_bound_passes_pick_most_washes_left_then_earliest_end(db):
    s = await fb.rig(db)
    hatch = await fb.get_hatchback_type_id(db)
    car_c = await make_vehicle(db, s["cu"]["id"], hatch, is_default=False)
    few = await _car_pass(db, s["cu"]["id"], s["cu"]["vehicle_id"], washes=2)
    many = await _car_pass(db, s["cu"]["id"], car_c, washes=4)
    _quote, (first,) = await _quote_then_book(db, s, [await _line(db)])
    assert first["subscription_id"] == many and first["vehicle_id"] == car_c
    # Two cars on one visit: each takes its own car's pass.
    two = QuickBookingLine(vehicle_type=hatch, quantity=2, service_ids=[await fb.get_star_wash_service_id(db)])
    _quote, cars = await _quote_then_book(db, s, [two], slot=1)
    assert {c["subscription_id"] for c in cars} == {few, many}
    assert {c["vehicle_id"] for c in cars} == {s["cu"]["vehicle_id"], car_c}
    assert all(c["total_amount"] == 0 for c in cars)


async def test_older_single_create_stamps_the_car(db):
    s = await fb.rig(db)
    sub_id = await _car_pass(db, s["cu"]["id"], s["cu"]["vehicle_id"])
    from app.schemas.booking_schema import BookingCreateRequest

    made = await BookingService(db).create_self_service_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"],
        service_ids=[await fb.get_star_wash_service_id(db)], scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ))
    d = await fb.doc(db, made["id"])
    assert d["subscription_id"] == sub_id and d["vehicle_id"] == s["cu"]["vehicle_id"] and d["total_amount"] == 0
