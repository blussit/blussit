"""Feature build 2026-10-07 — plans integration (contract with PLANS):
a custom multi-car pass (per-service quotas, car-bound) is used only when a
booking line names its car and the pass explicitly; exactly the covered
services are waived (each once), add-ons and other services are paid; the
1-hour plan-cancel rule applies to it too."""
import itertools

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import QuickBookingLine, QuickBookingRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests.factories import make_vehicle

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


async def _custom_pass(db, s: dict) -> dict:
    """Car A: 2 Star Wash + 2 Deep Cleaning; car B (SUV): 1 Deep + 2 Star —
    sold by the center's manager for cash, activated (PLANS' real path)."""
    from datetime import datetime, timezone

    from app.schemas.custom_plan_schema import CustomPlanCreateRequest
    from app.services.custom_plan_service import CustomPlanService

    cu = s["cu"]["id"]
    hatch, suv = await fb.get_hatchback_type_id(db), await fb.get_suv_type_id(db)
    car_a = await make_vehicle(db, cu, hatch, registration_number=f"MP09FB{next(_seq):04d}")
    car_b = await make_vehicle(db, cu, suv, registration_number=f"MP09FC{next(_seq):04d}")
    svc = {x["slug"]: str(x["_id"]) for x in await db.services.find({"slug": {"$in": ["star-wash", "deep-cleaning", "exterior-polish"]}}).to_list(5)}
    cars = [
        {"vehicle_id": car_a, "items": [{"service_id": svc["star-wash"], "count": 2}, {"service_id": svc["deep-cleaning"], "count": 2}]},
        {"vehicle_id": car_b, "items": [{"service_id": svc["deep-cleaning"], "count": 1}, {"service_id": svc["star-wash"], "count": 2}]},
    ]
    await db.bookings.insert_one({"customer_id": cu, "service_center_id": s["center_id"], "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKFB{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    mgr = {"actor_id": s["mgr"]["id"], "actor_role": "manager", "actor_center_id": s["center_id"]}
    service = CustomPlanService(db)
    cart = await service.create(CustomPlanCreateRequest(customer_id=cu, cars=cars, discount_amount=0), **mgr)
    await service.mark_cash_paid(cart["id"], expected_revision=cart["revision"], note=None, **mgr)
    subs = await db.user_subscriptions.find({"custom_plan_id": cart["id"]}).to_list(5)
    pass_a = next(x for x in subs if x["vehicle_id"] == car_a)
    pass_b = next(x for x in subs if x["vehicle_id"] == car_b)
    return {"car_a": car_a, "car_b": car_b, "hatch": hatch, "suv": suv, "svc": svc, "pass_a": str(pass_a["_id"]), "pass_b": str(pass_b["_id"])}


def _req(s, line: QuickBookingLine, slot: str) -> QuickBookingRequest:
    return QuickBookingRequest(
        customer_name="Custom Pass", customer_phone="9876543210", address_id=s["cu"]["address_id"], lines=[line],
        scheduled_date=s["when"], scheduled_slot=slot, payment_method="cash",
    )


async def test_custom_pass_waives_covered_service_add_on_paid_other_car_refused(db):
    s = await fb.rig(db)
    cp = await _custom_pass(db, s)
    customer = await db.users.find_one({"_id": ObjectId(s["cu"]["id"])})
    svc = BookingService(db)
    line = QuickBookingLine(vehicle_type=cp["hatch"], vehicle_id=cp["car_a"], subscription_id=cp["pass_a"],
                            service_ids=[cp["svc"]["star-wash"], cp["svc"]["exterior-polish"]])
    # The quote prices it exactly as the booking will.
    quote = await svc.quote_visit(customer_id=s["cu"]["id"], phone=customer["phone"], lines=[line], scheduled_date=s["when"])
    out = await svc.create_quick_booking(_req(s, line, s["keys"][0]), customer=customer, source="app")
    d = await fb.doc(db, out["bookings"][0]["id"])
    polish = await db.services.find_one({"slug": "exterior-polish"})
    star = await db.services.find_one({"slug": "star-wash"})
    assert d["vehicle_id"] == cp["car_a"] and d["subscription_id"] == cp["pass_a"]
    assert d["discount_amount"] == BookingService._resolve_price(star, cp["hatch"], d["first_time_eligible"])
    assert d["total_amount"] == BookingService._resolve_price(polish, cp["hatch"], False) == quote["total_amount"]
    assert d["subscription_consumption"] == {"by_service": {cp["svc"]["star-wash"]: 1}, "covered_service_ids": [cp["svc"]["star-wash"]]}
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(cp["pass_a"])})
    assert raw["remaining_by_service"][cp["svc"]["star-wash"]] == 1
    # Car B with car A's pass: refused — a car-bound pass never moves cars.
    wrong = QuickBookingLine(vehicle_type=cp["suv"], vehicle_id=cp["car_b"], subscription_id=cp["pass_a"], service_ids=[cp["svc"]["star-wash"]])
    with pytest.raises(BadRequestException):
        await svc.create_quick_booking(_req(s, wrong, s["keys"][1]), customer=customer, source="app")
    # ... and a type-only line never picks a car-bound pass up by itself.
    plain = QuickBookingLine(vehicle_type=cp["hatch"], service_ids=[cp["svc"]["star-wash"]])
    out2 = await svc.create_quick_booking(_req(s, plain, s["keys"][2]), customer=customer, source="app")
    assert (await fb.doc(db, out2["bookings"][0]["id"]))["subscription_id"] is None
    # Someone else's car: 404.
    other = await h.customer(db, s["pin"])
    stranger = QuickBookingLine(vehicle_type=cp["hatch"], vehicle_id=other["vehicle_id"], service_ids=[cp["svc"]["star-wash"]])
    from app.core.exceptions import NotFoundException

    with pytest.raises(NotFoundException):
        await svc.create_quick_booking(_req(s, stranger, s["keys"][1]), customer=customer, source="app")


async def test_custom_pass_cancel_follows_the_one_hour_rule(db):
    s = await fb.rig(db)
    cp = await _custom_pass(db, s)
    customer = await db.users.find_one({"_id": ObjectId(s["cu"]["id"])})
    svc = BookingService(db)
    deep = cp["svc"]["deep-cleaning"]

    async def book(slot):
        line = QuickBookingLine(vehicle_type=cp["hatch"], vehicle_id=cp["car_a"], subscription_id=cp["pass_a"], service_ids=[deep])
        return (await svc.create_quick_booking(_req(s, line, slot), customer=customer, source="app"))["bookings"][0]["id"]

    async def left():
        return (await db.user_subscriptions.find_one({"_id": ObjectId(cp["pass_a"])}))["remaining_by_service"][deep]

    early = await book(s["keys"][0])
    assert await left() == 1
    await fb.slot_in(db, early, 61)
    await svc.cancel_booking(early, fb.cancel(), s["cu"]["id"], "customer")
    assert await left() == 2  # returned
    late = await book(s["keys"][1])
    await fb.slot_in(db, late, 59)
    out = await svc.cancel_booking(late, fb.cancel(), s["cu"]["id"], "customer")
    assert out["plan_wash_forfeited"] is True and await left() == 1  # used up
    assert await fb.balance(db, s["cu"]["id"]) == 0
