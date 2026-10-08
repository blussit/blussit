"""
Society plans (docs/SOCIETY_PLANS.md) — pricing, enrollment, activation,
quotas, renewal, online payment and the "hidden from the website" rule.
Service-level; the Razorpay client is stubbed (no external call is made).
"""
from types import SimpleNamespace
from unittest.mock import ANY
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.schemas.payment_schema import CreateOrderRequest, VerifyPaymentRequest
from app.schemas.society_schema import CustomCombo, PremiumBookingRequest, SocietyEnrollRequest
from app.schemas.subscription_schema import ManagerSubscriptionPreviewRequest, SubscribeRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, _expected_signature
from app.services.society_service import (
    SocietyService,
    bucket_label,
    custom_price,
    ensure_society_lead_time,
    plan_price_for,
)
from app.services.subscription_service import SubscriptionPlanService, UserSubscriptionService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, get_suv_type_id
from tests.society_factories import activate_cash, auth, client, enroll, make_center, make_resident, make_society, make_template, plate, staff

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_soc_{next(_seq):06d}", **payload}


class _StubClient:
    order = _StubOrders()
    # verify asks Razorpay what the signed payment is (PAY-09): captured,
    # for the order and amount being verified (mock.ANY).
    payment = SimpleNamespace(fetch=lambda pid: {"id": pid, "status": "captured", "order_id": ANY, "amount": ANY, "currency": "INR"})


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=0)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    return {"center": center, "society": society, "plan": plan}


def _tomorrow() -> str:
    return (now_ist().date() + timedelta(days=1)).isoformat()


# -- pure pricing ---------------------------------------------------------------


async def test_price_helpers():
    plan = {
        "price": 2000, "discounted_price": 1649, "vehicle_type_prices": {"suv": 2400},
        "vehicle_type_discounted_prices": {"suv": 1999}, "vehicle_types": [],
    }
    assert plan_price_for(plan, "hatch") == (1649, 2000)
    assert plan_price_for(plan, "suv") == (1999, 2400)
    assert plan_price_for({**plan, "vehicle_types": ["suv"]}, "hatch") is None
    card = {"bucket_day_price": {"default": 38, "by_type": {"suv": 45}}, "bucket_day_mrp": {"default": 52, "by_type": {}}, "premium_discount_percent": 10}
    service = {"price": 349, "vehicle_type_prices": {"suv": 369}}
    # 25 x 38 + 2 x 349 x 0.9 = 950 + 628.2 -> 1578; MRP 25 x 52 + 698 = 1998
    assert custom_price(card, service, "hatch", 25, 2) == (1578, 1998)
    # SUV: 25 x 45 + 2 x 369 x 0.9 = 1125 + 664.2 -> 1789; MRP 1300 + 738 = 2038
    assert custom_price(card, service, "suv", 25, 2) == (1789, 2038)
    assert bucket_label(25) == "Daily wash" and bucket_label(15) == "Alternate-day wash" and bucket_label(10) == "10 washes a month"


# -- hidden from every general path -------------------------------------------------


async def test_society_plans_are_hidden_from_the_website_and_general_purchase(db, cleanup, rig, gateway):
    plan_id = rig["plan"]["id"]
    public = await SubscriptionPlanService(db).list_all(active_only=True)
    every = await SubscriptionPlanService(db).list_all(active_only=False)
    assert plan_id not in {p["id"] for p in public} | {p["id"] for p in every}
    with pytest.raises(NotFoundException):
        await SubscriptionPlanService(db).get(plan_id)

    resident = await make_resident(db, cleanup)
    hatchback = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    subs = UserSubscriptionService(db)
    with pytest.raises(NotFoundException):
        await subs.validate_purchase(str(resident["_id"]), SubscribeRequest(plan_id=plan_id, vehicle_type=hatchback, service_id=star))
    with pytest.raises(NotFoundException):
        await subs.quote_pass(str(resident["_id"]), plan_id, None, star, vehicle_type=hatchback)
    with pytest.raises(NotFoundException):
        await subs.resolve_service_price(plan_id, hatchback, star)
    # Customer checkout refuses it before any order is minted.
    with pytest.raises(NotFoundException):
        await PaymentService(db).create_order(
            str(resident["_id"]), CreateOrderRequest(purpose="subscription", plan_id=plan_id, vehicle_type=hatchback, service_id=star)
        )
    # The manager's "Sell a plan" form too.
    with pytest.raises(NotFoundException):
        await PaymentService(db).manager_subscription_preview(
            ManagerSubscriptionPreviewRequest(plan_id=plan_id, vehicle_type=hatchback, service_id=star)
        )
    assert await db.payment_orders.count_documents({"customer_id": str(resident["_id"])}) == 0
    # A manager's "discontinue plan" (any website plan) can't pull an
    # admin-owned society plan shared by every society.
    with pytest.raises(NotFoundException):
        await SubscriptionPlanService(db).discontinue(plan_id)
    assert (await db.subscription_plans.find_one({"_id": ObjectId(plan_id)}))["is_active"] is True


# -- enrollment + activation ---------------------------------------------------------


async def test_request_then_cash_activation_gives_each_car_its_own_quota(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=2)
    assert view["status"] == "requested"
    assert view["total_amount"] == 2 * 1649 and view["mrp_total"] == 2 * 2000
    assert all(c["status"] == "pending" and c["price"] == 1649 for c in view["cars"])
    address = await db.addresses.find_one({"owner_id": str(resident["_id"]), "society_id": rig["society"]["id"]})
    assert address and address["latitude"] == pytest.approx(rig["center"]["lat"] + 0.001)

    result = await activate_cash(db, view["id"])
    assert result["activated"] == 2 and not result["skipped"]
    enrollment = result["enrollment"]
    assert enrollment["status"] == "active"
    subs = await db.user_subscriptions.find({"enrollment_id": view["id"]}).to_list(None)
    assert len(subs) == 2
    star = await get_star_wash_service_id(db)
    for s in subs:
        assert s["society_id"] == rig["society"]["id"] and s["service_id"] == star
        assert s["remaining_service_count"] == 2 and s["total_service_count"] == 2
        assert s["purchased_price"] == 1649 and s["amount_paid"] == 1649 and s["payment_method"] == "cash"
        assert s["bucket_days"] == 25 and s["service_center_id"] == rig["center"]["id"]
        assert s["vehicle_id"] and s["plan_id"] == rig["plan"]["id"]
    ledger = await db.society_payments.find({"enrollment_id": view["id"]}).to_list(None)
    assert [(r["amount"], r["method"], r["kind"], r["car_count"]) for r in ledger] == [(3298, "cash", "activation", 2)]
    # Activating twice can't double the passes.
    with pytest.raises(BadRequestException, match="already active"):
        await activate_cash(db, view["id"])
    # The resident's own plan list labels them as society passes.
    mine = await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"]))
    society_passes = [s for s in mine if s.get("society_id")]
    assert len(society_passes) == 2
    assert all(s["plan_name"].endswith(rig["society"]["name"]) and s["society_name"] == rig["society"]["name"] for s in society_passes)


async def test_resubmitting_the_form_replaces_the_open_request(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    first = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    second = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=3)
    assert first["id"] == second["id"] and len(second["cars"]) == 3
    assert await db.society_enrollments.count_documents({"society_id": rig["society"]["id"], "customer_id": str(resident["_id"])}) == 1


async def test_two_simultaneous_submits_leave_one_open_request(db, cleanup, rig):
    import asyncio

    resident = await make_resident(db, cleanup)
    first, second = await asyncio.gather(
        enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1),
        enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1),
    )
    assert first["id"] == second["id"]
    assert await db.society_enrollments.count_documents({"society_id": rig["society"]["id"], "customer_id": str(resident["_id"])}) == 1


async def test_a_car_on_a_live_pass_is_refused(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    await activate_cash(db, view["id"])
    car_plate = view["cars"][0]["registration_number"]
    raw = await SocietyService(db).societies.find_by_id(rig["society"]["id"])
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await SocietyService(db).enroll(
            raw,
            SocietyEnrollRequest(plan_id=rig["plan"]["id"], resident_name="Again", phone=resident["phone"], flat="B-1",
                                 cars=[{"vehicle_type": await get_hatchback_type_id(db), "registration_number": car_plate}]),
            resident, source="form",
        )


async def test_customised_combo_becomes_a_reusable_society_plan(db, cleanup, rig):
    star = await get_star_wash_service_id(db)
    combo = CustomCombo(bucket_days=15, premium_service_id=star, premium_count=4)
    a = await make_resident(db, cleanup)
    b = await make_resident(db, cleanup)
    first = await enroll(db, rig["society"], a, custom=combo, cars=1)
    second = await enroll(db, rig["society"], b, custom=combo, cars=1)
    assert first["plan_id"] == second["plan_id"]
    cleanup.append(("subscription_plans", {"_id": ObjectId(first["plan_id"])}))
    plan = await db.subscription_plans.find_one({"_id": ObjectId(first["plan_id"])})
    assert plan["plan_type"] == "society" and plan["auto_created"] and plan["society_ids"] == [rig["society"]["id"]]
    assert plan["society_bucket_days"] == 15 and plan["total_service_count"] == 4
    hatchback = await get_hatchback_type_id(db)
    service = await db.services.find_one({"_id": ObjectId(star)})
    card = await SocietyService(db).rate_card()
    assert first["cars"][0]["price"] == custom_price(card, service, hatchback, 15, 4)[0]
    # Offered to neighbours in this society — never in another one.
    raw = await SocietyService(db).societies.find_by_id(rig["society"]["id"])
    assert first["plan_id"] in {p["id"] for p in await SocietyService(db).plans_for(raw, None)}
    other = await make_society(db, cleanup, rig["center"])
    other_raw = await SocietyService(db).societies.find_by_id(other["id"])
    assert first["plan_id"] not in {p["id"] for p in await SocietyService(db).plans_for(other_raw, None)}
    # A combination outside the admin's options is refused.
    with pytest.raises(BadRequestException):
        await enroll(db, rig["society"], a, custom=CustomCombo(bucket_days=7, premium_service_id=star, premium_count=4), cars=1)


async def test_personal_plan_is_only_offered_to_that_customer(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    stranger = await make_resident(db, cleanup)
    personal = await make_template(db, cleanup, scope="customer", customer_phone=resident["phone"], price=1299, mrp=1500)
    service = SocietyService(db)
    raw = await service.societies.find_by_id(rig["society"]["id"])
    assert personal["id"] in {p["id"] for p in await service.plans_for(raw, str(resident["_id"]))}
    assert personal["id"] not in {p["id"] for p in await service.plans_for(raw, str(stranger["_id"]))}
    assert personal["id"] not in {p["id"] for p in await service.plans_for(raw, None)}
    with pytest.raises(NotFoundException):
        await enroll(db, rig["society"], stranger, personal["id"], cars=1)
    view = await enroll(db, rig["society"], resident, personal["id"], cars=1)
    assert view["cars"][0]["price"] == 1299


async def test_per_type_prices(db, cleanup, rig):
    suv = await get_suv_type_id(db)
    plan = await make_template(db, cleanup, type_prices={suv: 1999})
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, plan["id"], cars=1, vehicle_type=suv)
    assert view["cars"][0]["price"] == 1999


# -- premium quota through the normal booking machinery -----------------------------


async def test_premium_booking_spends_quota_and_needs_a_days_notice(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    service = SocietyService(db)
    customer_id = str(resident["_id"])

    # Today: refused on the customer's channel — by the society endpoint
    # AND by the generic booking path (lead time lives in create_booking).
    today = now_ist().date().isoformat()
    with pytest.raises(BadRequestException, match="day ahead"):
        await service.book_premium(PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=today, scheduled_slot="17:00-20:00"),
                                   actor_id=customer_id, actor_role="customer", actor_center_id=None)
    with pytest.raises(BadRequestException, match="day ahead"):
        await ensure_society_lead_time(db, sub_id, datetime.strptime(today, "%Y-%m-%d"), "app")
    await ensure_society_lead_time(db, sub_id, datetime.strptime(today, "%Y-%m-%d"), "staff")  # staff exempt

    booked = await service.book_premium(
        PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_tomorrow(), scheduled_slot="08:00-11:00"),
        actor_id=customer_id, actor_role="customer", actor_center_id=None,
    )
    booking = await db.bookings.find_one({"_id": ObjectId(booked["bookings"][0]["id"])})
    assert booking["subscription_id"] == sub_id and booking["total_amount"] == 0 and booking["payment_status"] == "paid"
    assert booking["service_center_id"] == rig["center"]["id"]
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 1

    # Cancelling gives the wash back (existing restore_consumption).
    await BookingService(db).cancel_booking(booking_id=str(booking["_id"]), payload=BookingCancelRequest(reason="Changed plans"),
                                            actor_id=customer_id, actor_role="customer")
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"] == 2


async def test_society_pass_stays_active_at_zero_premium_washes(db, cleanup, rig):
    plan = await make_template(db, cleanup, count=1, price=999, mrp=1200)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, plan["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    vehicle_id = enrollment["cars"][0]["vehicle_id"]
    address = await db.addresses.find_one({"owner_id": str(resident["_id"]), "society_id": rig["society"]["id"]})
    await BookingService(db).create_booking(
        str(resident["_id"]),
        BookingCreateRequest(vehicle_id=vehicle_id, address_id=str(address["_id"]), service_ids=[await get_star_wash_service_id(db)],
                             scheduled_date=datetime.strptime(_tomorrow(), "%Y-%m-%d"), scheduled_slot="11:00-14:00", subscription_id=sub_id),
    )
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 0 and sub["status"] == "active"  # bucket washes continue
    with pytest.raises(BadRequestException, match="No premium washes left"):
        await SocietyService(db).book_premium(
            PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_tomorrow(), scheduled_slot="14:00-17:00"),
            actor_id=str(resident["_id"]), actor_role="customer", actor_center_id=None,
        )


# -- renewal -----------------------------------------------------------------------


async def test_renewal_window_and_early_carry_over(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    service = SocietyService(db)
    with pytest.raises(BadRequestException, match="Renewal opens"):
        await service.renew(await service.get_enrollment(view["id"]), method="cash", actor_id="m")

    # Two days before the end, one premium wash left: renewing now chains
    # the next 30 days after the current end and keeps the unused wash.
    old_end = datetime.now(timezone.utc) + timedelta(days=2)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": old_end, "remaining_service_count": 1}})
    result = await service.renew(await service.get_enrollment(view["id"]), method="cash", actor_id="m")
    assert result["renewed"] == 1 and result["amount"] == 1649
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 3 and sub["total_service_count"] == 3
    # The next plan month: same day next month (docs §2 "Plan month").
    from app.services.society_month import plan_month_end

    expected_end = plan_month_end(old_end).astimezone(timezone.utc).replace(tzinfo=None)
    assert abs((sub["end_date"] - expected_end).total_seconds()) < 2
    assert abs((sub["cycle_start"] - old_end.replace(tzinfo=None)).total_seconds()) < 2
    assert sub["prev_cycle_start"] is not None and sub["renewal_count"] == 1
    kinds = [r["kind"] for r in await db.society_payments.find({"enrollment_id": view["id"]}).sort("created_at", 1).to_list(None)]
    assert kinds == ["activation", "renewal"]

    # After it lapsed: a fresh cycle from now with a fresh quota.
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": datetime.now(timezone.utc) - timedelta(days=1), "status": "expired"}})
    await service.renew(await service.get_enrollment(view["id"]), method="cash", actor_id="m")
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["status"] == "active" and sub["remaining_service_count"] == 2 and sub["prev_cycle_start"] is None


async def test_cancel_one_car_keeps_the_other(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=2)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    first_vehicle = enrollment["cars"][0]["vehicle_id"]
    service = SocietyService(db)
    after = await service.cancel(await service.get_enrollment(view["id"]), vehicle_ids=[first_vehicle], actor_id="m")
    assert after["status"] == "active"
    assert [c["status"] for c in after["cars"]] == ["cancelled", "active"]
    statuses = {s["vehicle_id"]: s["status"] for s in await db.user_subscriptions.find({"enrollment_id": view["id"]}).to_list(None)}
    assert statuses[first_vehicle] == "cancelled" and list(statuses.values()).count("active") == 1
    after = await service.cancel(await service.get_enrollment(view["id"]), vehicle_ids=None, actor_id="m")
    assert after["status"] == "cancelled"


# -- online payment ------------------------------------------------------------------


async def test_online_payment_activates_through_the_shared_claim(db, cleanup, rig, gateway):
    resident = await make_resident(db, cleanup)
    customer_id = str(resident["_id"])
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=2, pay_now=True)
    assert view["status"] == "awaiting_payment"
    payments = PaymentService(db)
    order = await payments.create_order(customer_id, CreateOrderRequest(purpose="society", society_enrollment_id=view["id"]))
    assert order["amount"] == 2 * 1649 * 100
    # Someone else's enrollment id is a 404, not a quote.
    stranger = await make_resident(db, cleanup)
    with pytest.raises(NotFoundException):
        await payments.create_order(str(stranger["_id"]), CreateOrderRequest(purpose="society", society_enrollment_id=view["id"]))

    result = await payments.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_soc_ok",
        razorpay_signature=_expected_signature(order["order_id"], "pay_soc_ok")))
    assert result["status"] == "paid" and result["society_enrollment_id"] == view["id"]
    enrollment = await db.society_enrollments.find_one({"_id": ObjectId(view["id"])})
    assert enrollment["status"] == "active" and enrollment["payment"]["method"] == "online"
    assert await db.user_subscriptions.count_documents({"enrollment_id": view["id"], "payment_method": "online"}) == 2
    # A replayed verify is a no-op.
    again = await payments.verify_payment(customer_id, VerifyPaymentRequest(
        razorpay_order_id=order["order_id"], razorpay_payment_id="pay_soc_ok",
        razorpay_signature=_expected_signature(order["order_id"], "pay_soc_ok")))
    assert again["status"] == "paid"
    assert await db.user_subscriptions.count_documents({"enrollment_id": view["id"]}) == 2


async def test_payment_for_a_changed_request_is_parked_not_applied(db, cleanup, rig, gateway):
    resident = await make_resident(db, cleanup)
    customer_id = str(resident["_id"])
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1, pay_now=True)
    order = await PaymentService(db).create_order(customer_id, CreateOrderRequest(purpose="society", society_enrollment_id=view["id"]))
    # The resident edits the request (3 cars now) after the order was minted.
    await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=3, pay_now=True)
    with pytest.raises(BadRequestException):
        await PaymentService(db).verify_payment(customer_id, VerifyPaymentRequest(
            razorpay_order_id=order["order_id"], razorpay_payment_id="pay_soc_stale",
            razorpay_signature=_expected_signature(order["order_id"], "pay_soc_stale")))
    doc = await db.payment_orders.find_one({"razorpay_order_id": order["order_id"]})
    assert doc["status"] == "paid_attention"
    assert await db.user_subscriptions.count_documents({"enrollment_id": view["id"]}) == 0
    cleanup.append(("notifications", {"title": "Payment needs attention"}))


async def test_requests_degrade_to_pay_later_without_razorpay(db, cleanup, rig, monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "")
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1, pay_now=True)
    assert view["status"] == "requested"
    raw = await SocietyService(db).societies.find_by_id(rig["society"]["id"])
    assert (await SocietyService(db).public_form(raw, None))["online_payment"] is False


async def test_plate_validation_and_duplicates():
    with pytest.raises(ValueError):
        SocietyEnrollRequest(plan_id="x", resident_name="A B", phone="9876543210", flat="1", cars=[{"vehicle_type": "t", "registration_number": "NOTAPLATE"}])
    p = plate()
    with pytest.raises(ValueError):
        SocietyEnrollRequest(plan_id="x", resident_name="A B", phone="9876543210", flat="1",
                             cars=[{"vehicle_type": "t", "registration_number": p}, {"vehicle_type": "t", "registration_number": p}])


async def test_resident_cannot_self_cancel_or_upgrade_a_society_pass(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    sub_id = (await activate_cash(db, view["id"]))["enrollment"]["cars"][0]["subscription"]["id"]
    subs = UserSubscriptionService(db)
    with pytest.raises(BadRequestException, match="society manager"):
        await subs.cancel(str(resident["_id"]), sub_id)
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["status"] == "active"


# -- review fixes (2026-10-03) -------------------------------------------------------


async def test_a_stale_second_cash_renewal_never_renews_twice(db, cleanup, rig, monkeypatch):
    """Two cash renewals racing (a double tap): the second read the pass
    before the first wrote. It must stop — not add a second cycle's quota
    and a second cash row for one payment."""
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(sub_id)}, {"$set": {"end_date": datetime.now(timezone.utc) + timedelta(days=2), "remaining_service_count": 1}}
    )
    service = SocietyService(db)
    fresh = await service.get_enrollment(view["id"])
    stale = await service._renewable_subs(fresh)  # what the second tap read

    first = await service.renew(fresh, method="cash", actor_id="m")
    assert first["renewed"] == 1

    async def _stale(_enrollment):
        return stale

    monkeypatch.setattr(service, "_renewable_subs", _stale)
    second = await service.renew(fresh, method="cash", actor_id="m")
    assert second["renewed"] == 0 and second["amount"] == 0
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 3 and sub["renewal_count"] == 1
    assert await db.society_payments.count_documents({"enrollment_id": view["id"], "kind": "renewal"}) == 1


async def test_renewal_skips_a_car_that_is_on_another_pass_now(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    car = enrollment["cars"][0]
    # The society pass lapsed, and the car meanwhile got another live pass.
    await db.user_subscriptions.update_one(
        {"_id": ObjectId(car["subscription"]["id"])}, {"$set": {"end_date": datetime.now(timezone.utc) - timedelta(days=1), "status": "expired"}}
    )
    other = await db.user_subscriptions.insert_one({
        "customer_id": str(resident["_id"]), "vehicle_id": car["vehicle_id"], "status": "active", "is_deleted": False,
        "plan_id": rig["plan"]["id"], "remaining_service_count": 1, "total_service_count": 1,
        "end_date": datetime.now(timezone.utc) + timedelta(days=20), "start_date": datetime.now(timezone.utc),
    })
    cleanup.append(("user_subscriptions", {"_id": other.inserted_id}))
    service = SocietyService(db)
    with pytest.raises(BadRequestException, match="another active plan"):
        await service.renew(await service.get_enrollment(view["id"]), method="cash", actor_id="m")
    # An online renewal already paid for it is NOT applied either — the
    # payment service parks that order for a human instead of putting two
    # live passes on one car.
    with pytest.raises(BadRequestException, match="Nothing left to renew"):
        await service.renew(await service.get_enrollment(view["id"]), method="online", actor_id=None,
                            order={"razorpay_order_id": "order_x"}, subscription_ids=[car["subscription"]["id"]])
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(car["subscription"]["id"])})
    assert sub["status"] == "expired"


async def test_resubmitting_a_car_on_a_live_pass_with_another_type_does_not_retype_it(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    car = enrollment["cars"][0]
    suv = await get_suv_type_id(db)
    payload = SocietyEnrollRequest(
        plan_id=rig["plan"]["id"], resident_name="Resident", phone=resident["phone"], flat="B-9",
        cars=[{"vehicle_type": suv, "registration_number": car["registration_number"]}],
    )
    raw = await SocietyService(db).societies.find_by_id(rig["society"]["id"])
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await SocietyService(db).enroll(raw, payload, resident, source="form")
    vehicle = await db.vehicles.find_one({"_id": ObjectId(car["vehicle_id"])})
    assert vehicle["vehicle_type"] == car["vehicle_type"]


async def test_rescheduling_a_premium_wash_to_today_keeps_the_days_notice(db, cleanup, rig):
    from app.schemas.booking_schema import BookingRescheduleRequest

    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    customer_id = str(resident["_id"])
    booked = await SocietyService(db).book_premium(
        PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_tomorrow(), scheduled_slot="08:00-11:00"),
        actor_id=customer_id, actor_role="customer", actor_center_id=None,
    )
    booking_id = booked["bookings"][0]["id"]
    today = datetime.strptime(now_ist().date().isoformat(), "%Y-%m-%d")
    with pytest.raises(BadRequestException, match="day ahead"):
        await BookingService(db).reschedule_booking(
            booking_id, BookingRescheduleRequest(scheduled_date=today, scheduled_slot="17:00-20:00"), customer_id, "customer",
        )
    booking = await db.bookings.find_one({"_id": ObjectId(booking_id)})
    assert booking["scheduled_slot"] == "08:00-11:00"


async def test_society_schema_rejects_negative_and_oversized_inputs():
    from pydantic import ValidationError

    from app.schemas.society_schema import CancelEnrollmentRequest, RateTable, SocietyPlanUpdateRequest

    with pytest.raises(ValidationError):
        SocietyPlanUpdateRequest(vehicle_type_prices={"x": -5})
    with pytest.raises(ValidationError):
        RateTable(default=10, by_type={"x": -1})
    with pytest.raises(ValidationError):
        CancelEnrollmentRequest(vehicle_ids=[str(i) for i in range(50)])
    assert SocietyPlanUpdateRequest(vehicle_type_prices={"x": 0}).vehicle_type_prices == {"x": 0}


async def test_plan_list_links_a_society_pass_to_its_page_only_while_the_link_works(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    await activate_cash(db, view["id"])
    mine = [s for s in await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"])) if s.get("society_id")]
    assert mine and mine[0]["society_form_path"] == f"/society/{rig['society']['form_token']}"
    # Form switched off: the link still opens THIS resident's hub (only new
    # sign-ups stop — SocietyService.society_by_token), so it stays (SOC-8,
    # remediation 2026-10-07; this used to assert the link was hidden).
    await db.societies.update_one({"_id": ObjectId(rig["society"]["id"])}, {"$set": {"form_enabled": False}})
    mine = [s for s in await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"])) if s.get("society_id")]
    assert mine[0]["society_form_path"] == f"/society/{rig['society']['form_token']}" and mine[0]["society_name"] == rig["society"]["name"]
    # A cancelled plan has no hub to open: no dead link.
    await db.user_subscriptions.update_one({"_id": ObjectId(mine[0]["id"])}, {"$set": {"status": "cancelled"}})
    mine = [s for s in await UserSubscriptionService(db).list_my_subscriptions(str(resident["_id"])) if s.get("society_id")]
    assert mine[0]["society_form_path"] is None


async def test_mark_paid_activates_only_the_version_the_manager_reviewed(db, cleanup, rig):
    """The manager looks at a 1-car request; the resident resubmits 3 cars
    before "Mark paid" lands. Activation is refused (409) — nothing goes
    live and no cash is booked — until the manager reviews the new one."""
    from app.core.exceptions import ConflictException

    resident = await make_resident(db, cleanup)
    first = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    reviewed = first["revision"]
    await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=3)
    service = SocietyService(db)
    with pytest.raises(ConflictException):
        await service.activate(await service.get_enrollment(first["id"]), method="cash", actor_id="m", expected_revision=reviewed)
    stored = await db.society_enrollments.find_one({"_id": ObjectId(first["id"])})
    assert stored["status"] in ("requested", "awaiting_payment")
    assert await db.society_payments.count_documents({"enrollment_id": first["id"]}) == 0
    assert await db.user_subscriptions.count_documents({"enrollment_id": first["id"]}) == 0
    # The version on screen now goes through.
    fresh = await service.enrollment_view(await service.get_enrollment(first["id"]))
    assert fresh["revision"] == reviewed + 1
    result = await service.activate(await service.get_enrollment(first["id"]), method="cash", actor_id="m", expected_revision=fresh["revision"])
    assert result["activated"] == 3


async def test_mark_paid_over_http_needs_the_reviewed_revision(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    manager_id, _captain = await staff(db, cleanup, rig["center"]["id"])
    h = auth(manager_id, "manager", rig["center"]["id"])
    async with client() as c:
        missing = await c.post(f"/api/v1/society-enrollments/{view['id']}/activate", headers=h, json={"method": "cash"})
        assert missing.status_code == 400
        stale = await c.post(f"/api/v1/society-enrollments/{view['id']}/activate", headers=h, json={"method": "cash", "expected_revision": view["revision"] + 1})
        assert stale.status_code == 409
        ok = await c.post(f"/api/v1/society-enrollments/{view['id']}/activate", headers=h, json={"method": "cash", "expected_revision": view["revision"]})
        assert ok.status_code == 200, ok.text


async def test_staff_premium_booking_must_be_for_the_society_in_the_url(db, cleanup, rig):
    other = await make_society(db, cleanup, rig["center"])
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub_id = enrollment["cars"][0]["subscription"]["id"]
    with pytest.raises(NotFoundException):
        await SocietyService(db).book_premium(
            PremiumBookingRequest(subscription_ids=[sub_id], scheduled_date=_tomorrow(), scheduled_slot="08:00-11:00"),
            actor_id="m", actor_role="admin", actor_center_id=None, society_id=other["id"],
        )
    assert await db.bookings.count_documents({"subscription_id": sub_id}) == 0


async def test_sweep_only_captures_a_society_payment_that_is_still_payable(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    pay = PaymentService(db)
    order = {"purpose": "society", "society_enrollment_id": view["id"], "society_renewal": False, "society_revision": view["revision"]}
    assert await pay._target_still_payable(order) is True
    # Resubmitted since the order was priced -> not this order's to capture.
    await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=2)
    assert await pay._target_still_payable(order) is False
    fresh = await SocietyService(db).enrollment_view(await SocietyService(db).get_enrollment(view["id"]))
    assert await pay._target_still_payable({**order, "society_revision": fresh["revision"]}) is True
    # Activated for cash in the meantime -> nothing left to pay.
    await activate_cash(db, view["id"])
    assert await pay._target_still_payable({**order, "society_revision": fresh["revision"]}) is False
    assert await pay._target_still_payable({"purpose": "society", "society_enrollment_id": "nope"}) is False
