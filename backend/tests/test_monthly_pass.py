"""
Monthly passes (founder model, 2026-09-11).

Buying one answers exactly two questions — WHICH CAR and WHICH SERVICE —
and everything else follows from them: the car's type and the chosen
service set the price, the pass covers that one wash on that one car, and
one car carries one pass. Deep Cleaning is deliberately not sold as a pass.
"""
import itertools
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.schemas.subscription_schema import SubscribeRequest, SubscriptionPlanCreateRequest, SubscriptionPlanUpdateRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, _expected_signature
from app.services.subscription_service import (
    SubscriptionPlanService,
    UserSubscriptionService,
    resolve_pass_price,
    service_price_for_type,
)
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_suv_type_id,
    make_customer_with_vehicle,
    make_service_center,
    make_subscription_plan,
    make_vehicle,
)

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_pass_{next(_seq):06d}", **payload}


class _StubClient:
    order = _StubOrders()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    suv = await get_suv_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("user_subscriptions", {"customer_id": customer_id}))
    cleanup.append(("payment_orders", {"customer_id": customer_id}))

    mains = await db.services.find({"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}}).to_list(length=20)
    offered, excluded = mains[0], mains[1]
    addon = await db.services.find_one({"is_addon": True, "is_deleted": {"$ne": True}})
    plan_id = await make_subscription_plan(
        db, vehicle_types=[], included_service_ids=[str(offered["_id"])], total_service_count=4
    )
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    await db.subscription_plans.update_one({"_id": ObjectId(plan_id)}, {"$set": {"plan_discount_percent": 15.0}})
    return {
        "db": db, "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id,
        "plan_id": plan_id, "offered": offered, "excluded": excluded, "addon": addon,
        "hatchback": hatchback, "suv": suv, "center_id": center_id,
        "bike_type": str((await db.vehicle_types.find_one({"name": "Bike"}) or {}).get("_id", "")),
    }


# ------------------------------------------------------------- pricing


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_pass_price_uses_standard_not_first_time_pricing():
    """A pass is a month of washes — pricing it off the one-time
    introductory rate would undercharge every pass ever sold."""
    service = {"price": 400.0, "discounted_price": 199.0, "vehicle_type_prices": {"suv": 600.0},
               "vehicle_type_discounted_prices": {"suv": 299.0}}
    assert service_price_for_type(service, "suv") == 600.0
    assert service_price_for_type(service, "hatch") == 400.0
    assert service_price_for_type(service, None) == 400.0
    # 4 washes at 600, less 15% = 2040
    assert resolve_pass_price({"total_service_count": 4, "plan_discount_percent": 15.0}, service, "suv") == 2040.0
    # No discount configured = the plain sum.
    assert resolve_pass_price({"total_service_count": 4}, service, "hatch") == 1600.0


async def test_admin_set_price_wins_over_the_formula(rig, db, gateway):
    """Admin can price a pass at a round, sellable figure for a given wash
    type + vehicle type. That number is the whole monthly price, it beats
    the computed one, and the CHECKOUT must charge exactly it."""
    subscriptions = UserSubscriptionService(db)
    service_id = str(rig["offered"]["_id"])
    computed = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], rig["vehicle_id"], service_id)

    await db.subscription_plans.update_one(
        {"_id": ObjectId(rig["plan_id"])},
        {"$set": {f"service_pass_prices.{service_id}.{rig['hatchback']}": 999.0}},
    )
    quoted = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], rig["vehicle_id"], service_id)
    assert quoted["price"] == 999.0 != computed["price"]
    # No invented "% off" story on a hand-set price.
    assert quoted["discount_percent"] == 0.0

    order = await PaymentService(db).create_order(
        rig["customer_id"],
        CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=service_id),
    )
    assert order["amount"] == 99900

    # A DIFFERENT vehicle type on the same service is untouched by that
    # override — it keeps computing.
    other_car = await make_vehicle(db, rig["customer_id"], rig["suv"])
    suv_quote = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], other_car, service_id)
    assert suv_quote["price"] != 999.0


async def test_quote_is_what_the_checkout_charges(rig, db, gateway):
    """The purchase sheet's number and the Razorpay order's number come from
    the same function — a mismatch is how customers get charged a price they
    were never shown."""
    subscriptions = UserSubscriptionService(db)
    quote = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], rig["vehicle_id"], str(rig["offered"]["_id"]))
    assert quote["visits"] == 4 and quote["discount_percent"] == 15.0
    assert quote["price"] == round(quote["price_per_wash"] * 4 * 0.85)
    assert quote["vehicle_has_pass"] is False

    order = await PaymentService(db).create_order(
        rig["customer_id"],
        CreateOrderRequest(purpose="subscription", plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"],
                           service_id=str(rig["offered"]["_id"])),
    )
    assert order["amount"] == int(round(quote["price"] * 100))


# ------------------------------------------------------------ purchase


async def test_pass_records_the_car_and_the_service(rig, db):
    subscriptions = UserSubscriptionService(db)
    sub = await subscriptions.subscribe(
        rig["customer_id"],
        SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["offered"]["_id"])),
    )
    assert sub["vehicle_id"] == rig["vehicle_id"]
    assert sub["service_id"] == str(rig["offered"]["_id"])
    assert sub["vehicle_type"] == rig["hatchback"]  # taken from the CAR, not the client
    assert sub["purchased_price"] > 0
    assert sub["remaining_service_count"] == 4


async def test_a_service_not_on_the_menu_is_refused(rig, db):
    """Deep Cleaning isn't sold as a pass — nor is any service the admin
    left off this plan's menu."""
    subscriptions = UserSubscriptionService(db)
    with pytest.raises(BadRequestException, match="isn't available on this pass"):
        await subscriptions.subscribe(
            rig["customer_id"],
            SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["excluded"]["_id"])),
        )
    # ...and neither is an ADD-ON masquerading as the covered wash.
    if rig["addon"]:
        with pytest.raises(BadRequestException, match="isn't available on this pass"):
            await subscriptions.subscribe(
                rig["customer_id"],
                SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["addon"]["_id"])),
            )


async def test_the_service_has_to_fit_the_car(rig, db):
    """A bike wash on an SUV is a pass nobody can ever redeem. Hiding the
    option in the sheet is a convenience; THIS is the rule — the frontend is
    never the boundary."""
    subscriptions = UserSubscriptionService(db)
    bike_only = await db.services.find_one(
        {"is_addon": {"$ne": True}, "is_deleted": {"$ne": True}, "vehicle_types": [rig["bike_type"]]}
    )
    if not bike_only:
        pytest.skip("no bike-only service in the catalogue to exercise the rule")
    # Put it on the menu, so the ONLY thing that can refuse it is the fit.
    await db.subscription_plans.update_one(
        {"_id": ObjectId(rig["plan_id"])}, {"$addToSet": {"included_service_ids": str(bike_only["_id"])}}
    )
    with pytest.raises(BadRequestException, match="isn't offered for"):
        await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], rig["vehicle_id"], str(bike_only["_id"]))
    with pytest.raises(BadRequestException, match="isn't offered for"):
        await subscriptions.subscribe(
            rig["customer_id"],
            SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(bike_only["_id"])),
        )
    # The same service on a matching vehicle is fine — the rule is fit, not
    # a blanket ban.
    bike_vehicle = await make_vehicle(db, rig["customer_id"], rig["bike_type"])
    quote = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], bike_vehicle, str(bike_only["_id"]))
    assert quote["price"] > 0


async def test_choosing_no_service_is_refused(rig, db):
    subscriptions = UserSubscriptionService(db)
    with pytest.raises(BadRequestException, match="Choose which service"):
        await subscriptions.subscribe(
            rig["customer_id"], SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"])
        )


async def test_a_pass_can_only_be_bought_for_your_own_car(rig, db, cleanup):
    from tests.factories import make_customer

    stranger = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(stranger)}))
    cleanup.append(("user_subscriptions", {"customer_id": stranger}))
    with pytest.raises(NotFoundException):
        await UserSubscriptionService(db).subscribe(
            stranger,
            SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["offered"]["_id"])),
        )


async def test_one_car_carries_one_pass_but_a_second_car_is_fine(rig, db):
    subscriptions = UserSubscriptionService(db)
    await subscriptions.subscribe(
        rig["customer_id"],
        SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["offered"]["_id"])),
    )
    with pytest.raises(BadRequestException, match="already has an active pass"):
        await subscriptions.subscribe(
            rig["customer_id"],
            SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["offered"]["_id"])),
        )
    # A DIFFERENT car on the SAME plan is exactly what the model allows.
    second = await make_vehicle(db, rig["customer_id"], rig["suv"])
    other = await subscriptions.subscribe(
        rig["customer_id"],
        SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=second, service_id=str(rig["offered"]["_id"])),
    )
    assert other["vehicle_id"] == second
    # ...and the quote says so up front, before any payment starts.
    quote = await subscriptions.quote_pass(rig["customer_id"], rig["plan_id"], rig["vehicle_id"], str(rig["offered"]["_id"]))
    assert quote["vehicle_has_pass"] is True


# ----------------------------------------------------------- redemption


async def _pass_and_booking_args(db, rig):
    subscriptions = UserSubscriptionService(db)
    sub = await subscriptions.subscribe(
        rig["customer_id"],
        SubscribeRequest(plan_id=rig["plan_id"], vehicle_id=rig["vehicle_id"], service_id=str(rig["offered"]["_id"])),
    )
    return subscriptions, sub


async def test_pass_redeems_only_its_own_car(rig, db):
    subscriptions, sub = await _pass_and_booking_args(db, rig)
    other_car = await make_vehicle(db, rig["customer_id"], rig["hatchback"])
    with pytest.raises(BadRequestException, match="belongs to"):
        await subscriptions.plan_consumption(sub["id"], other_car, [rig["offered"]], rig["customer_id"])


async def test_pass_redeems_only_its_own_service(rig, db):
    subscriptions, sub = await _pass_and_booking_args(db, rig)
    with pytest.raises(BadRequestException, match="covers"):
        await subscriptions.plan_consumption(sub["id"], rig["vehicle_id"], [rig["excluded"]], rig["customer_id"])


async def test_addons_ride_along_without_consuming_a_wash(rig, db):
    subscriptions, sub = await _pass_and_booking_args(db, rig)
    services = [rig["offered"]] + ([rig["addon"]] if rig["addon"] else [])
    consumption = await subscriptions.plan_consumption(sub["id"], rig["vehicle_id"], services, rig["customer_id"])
    assert consumption == {"flat_count": 1}


async def test_someone_elses_pass_cannot_be_spent(rig, db, cleanup):
    """The subscription id travels through the browser — the ownership check
    is the whole defence against draining a stranger's washes."""
    from tests.factories import make_customer

    _subscriptions, sub = await _pass_and_booking_args(db, rig)
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(attacker)}))
    with pytest.raises(NotFoundException):
        await UserSubscriptionService(db).plan_consumption(sub["id"], rig["vehicle_id"], [rig["offered"]], attacker)


# ------------------------------------------------- booking with a pass


async def test_pass_booking_waives_the_wash_and_prepays_the_extras(rig, db):
    """The wash is covered; add-ons are a real charge — and a pass booking
    carrying a charge is PREPAID (founder rule: no cash option on those)."""
    subscriptions, sub = await _pass_and_booking_args(db, rig)
    booking_service = BookingService(db)
    when = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await booking_service.available_slots(rig["center_id"], when)
    slot = next(s["key"] for s in slots if s["status"] == "available")

    service_ids = [str(rig["offered"]["_id"])] + ([str(rig["addon"]["_id"])] if rig["addon"] else [])
    booking = await booking_service.create_booking(
        rig["customer_id"],
        BookingCreateRequest(
            vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=service_ids,
            scheduled_date=when, scheduled_slot=slot, subscription_id=sub["id"],
        ),
    )
    assert booking["payment_method"] == "subscription"
    if rig["addon"]:
        assert booking["total_amount"] > 0
        assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value  # prepaid, no cash option
    else:
        assert booking["total_amount"] == 0
    # Exactly one wash came off the pass.
    fresh = await db.user_subscriptions.find_one({"_id": ObjectId(sub["id"])})
    assert fresh["remaining_service_count"] == 3


# --------------------------------------------------------- monthly only


async def test_only_monthly_plans_can_be_sold(db):
    from app.models.enums import BillingCycle

    plans = SubscriptionPlanService(db)
    with pytest.raises(BadRequestException, match="monthly"):
        await plans.create(SubscriptionPlanCreateRequest(name="Yearly Thing", billing_cycle=BillingCycle.YEARLY, price=999))
    with pytest.raises(BadRequestException, match="monthly"):
        await plans.update("anything", SubscriptionPlanUpdateRequest(billing_cycle=BillingCycle.QUARTERLY))
