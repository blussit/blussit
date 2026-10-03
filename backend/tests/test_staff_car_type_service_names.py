"""
Car type + service on every staff list (founder, 2026-10: the plan drill
row "Monthly Shine · Plan Buyer · 3 Oct 2026 · Cash ₹1,396" must also say
WHICH car and WHICH wash — "Hatchback · Star Wash").

Pins the additive fields (existing fields/shapes untouched):
- plan_purchases rows: vehicle_type(_name) + service_id/service_name, from
  the order, falling back to the subscription it settled into;
- admin_overview / center_overview rows: the same, from the subscription;
- customer 360: plan rows carry both names, booking rows vehicle_type_name;
- manager dashboard flagged rows: vehicle_type_name + service_names;
- enriched bookings: vehicle_type_name (even for a saved-vehicle booking
  whose label is "Brand Model");
- admin collections attention rows: plan / car type / service names.
"""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.crm_service import CRMService
from app.services.kpi_service import resolve_period
from app.services.manager_dashboard_service import ManagerDashboardService
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist
from tests.factories import (
    get_any_active_plan,
    get_hatchback_type_id,
    get_star_wash_service_id,
    get_suv_type_id,
    make_customer,
    make_service_center,
    make_vehicle,
)


@pytest.fixture
async def rig(db, cleanup):
    center = await make_service_center(db)
    customer = await make_customer(db, name="Plan Buyer")
    hatch, suv = await get_hatchback_type_id(db), await get_suv_type_id(db)
    star = await get_star_wash_service_id(db)
    plan_id = await get_any_active_plan(db)
    now = now_ist()
    sub_with = {
        "customer_id": customer, "plan_id": plan_id, "service_center_id": center, "vehicle_type": hatch, "service_id": star,
        "status": "active", "start_date": now, "end_date": now + timedelta(days=30), "amount_paid": 1396.0,
        "total_service_count": 4, "remaining_service_count": 4, "created_at": now, "is_deleted": False,
    }
    # A legacy tier plan — no service, SUV tier.
    sub_legacy = {**sub_with, "vehicle_type": suv, "service_id": None, "created_at": now - timedelta(minutes=1)}
    sub_ids = (await db.user_subscriptions.insert_many([sub_with, sub_legacy])).inserted_ids
    orders = (await db.payment_orders.insert_many([
        # Stamped on the order itself (every current checkout path).
        {"purpose": "subscription", "status": "paid", "kind": "manager_cash", "service_center_id": center, "customer_id": customer,
         "plan_id": plan_id, "vehicle_type": hatch, "service_id": star, "amount_paise": 139600, "created_at": now - timedelta(minutes=5),
         "subscription_id": str(sub_ids[0])},
        # An older row without them — resolved through its subscription.
        {"purpose": "subscription", "status": "paid", "kind": "link", "service_center_id": center, "customer_id": customer,
         "plan_id": plan_id, "amount_paise": 99900, "created_at": now - timedelta(minutes=10), "subscription_id": str(sub_ids[0])},
    ])).inserted_ids
    cleanup.append(("payment_orders", {"_id": {"$in": orders}}))
    cleanup.append(("user_subscriptions", {"_id": {"$in": sub_ids}}))
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    hatch_name = (await db.vehicle_types.find_one({"_id": ObjectId(hatch)}))["name"]
    suv_name = (await db.vehicle_types.find_one({"_id": ObjectId(suv)}))["name"]
    star_name = (await db.services.find_one({"_id": ObjectId(star)}))["name"]
    return {
        "center": center, "customer": customer, "hatch": hatch, "suv": suv, "star": star, "plan_id": plan_id,
        "hatch_name": hatch_name, "suv_name": suv_name, "star_name": star_name, "sub_ids": [str(i) for i in sub_ids],
    }


@pytest.mark.asyncio
async def test_plan_purchase_rows_name_the_car_and_the_wash(db, rig):
    s, e, _, _ = resolve_period("today", None, None)
    svc = UserSubscriptionService(db)
    for center in (None, rig["center"]):  # admin (platform) and manager (center) drill-downs
        rows, total = await svc.plan_purchases(s, e, 1, 50, service_center_id=center, extra={"customer_id": rig["customer"]})
        assert total == 2
        for row in rows:
            # Existing shape intact …
            assert {"id", "customer_name", "plan_name", "amount", "payment_method", "created_at"} <= set(row)
            # … plus the car type and the one wash the pass covers, even on
            # the older order that never stamped them itself.
            assert (row["vehicle_type"], row["vehicle_type_name"]) == (rig["hatch"], rig["hatch_name"])
            assert (row["service_id"], row["service_name"]) == (rig["star"], rig["star_name"])
        assert rows[0]["customer_name"] == "Plan Buyer" and rows[0]["payment_method"] == "Cash"


@pytest.mark.asyncio
async def test_subscriber_lists_name_the_car_and_the_wash(db, rig):
    svc = UserSubscriptionService(db)
    admin = await svc.admin_overview(search="Plan Buyer")
    center = await svc.center_overview(rig["center"], "admin", None, search="Plan Buyer")
    for data in (admin, center):
        rows = {r["subscription_id"]: r for r in data["rows"] if r["customer_id"] == rig["customer"]}
        pass_row, legacy_row = rows[rig["sub_ids"][0]], rows[rig["sub_ids"][1]]
        assert (pass_row["vehicle_type_name"], pass_row["service_name"]) == (rig["hatch_name"], rig["star_name"])
        assert pass_row["service_id"] == rig["star"]
        # A legacy tier plan has a car type but no single service.
        assert (legacy_row["vehicle_type_name"], legacy_row["service_name"]) == (rig["suv_name"], None)


@pytest.mark.asyncio
async def test_customer_360_plans_and_bookings_carry_names(db, rig, cleanup):
    booking = {
        "booking_number": f"BK-NAMES-{ObjectId()}", "customer_id": rig["customer"], "service_center_id": rig["center"],
        "status": "completed", "service_ids": [rig["star"]], "vehicle_type": rig["hatch"], "vehicle_label": rig["hatch_name"],
        "total_amount": 349.0, "scheduled_date": now_ist().replace(tzinfo=None), "scheduled_slot": "09:00-12:00",
        "created_at": now_ist(), "is_deleted": False,
    }
    bid = (await db.bookings.insert_one(booking)).inserted_id
    cleanup.insert(0, ("bookings", {"_id": bid}))
    data = await CRMService(db).get_customer_360(rig["customer"], "admin", None)
    plans = {s["id"]: s for s in data["subscriptions"]}
    assert (plans[rig["sub_ids"][0]]["vehicle_type_name"], plans[rig["sub_ids"][0]]["service_name"]) == (rig["hatch_name"], rig["star_name"])
    assert plans[rig["sub_ids"][1]]["service_name"] is None
    row = next(b for b in data["bookings"] if b["booking_number"] == booking["booking_number"])
    assert row["vehicle_type_name"] == rig["hatch_name"] and row["service_names"] == [rig["star_name"]]


@pytest.mark.asyncio
async def test_enriched_booking_names_the_car_type_even_for_a_saved_vehicle(db, rig, cleanup):
    vehicle = await make_vehicle(db, rig["customer"], rig["suv"])
    cleanup.insert(0, ("vehicles", {"_id": ObjectId(vehicle)}))
    quick = {"_id": ObjectId(), "customer_id": rig["customer"], "service_ids": [rig["star"]], "vehicle_type": rig["hatch"], "vehicle_label": rig["hatch_name"]}
    saved = {"_id": ObjectId(), "customer_id": rig["customer"], "service_ids": [rig["star"]], "vehicle_id": vehicle}
    out = await BookingService(db)._enrich_bookings([quick, saved])
    assert out[0]["vehicle_type_name"] == rig["hatch_name"]
    assert out[1]["vehicle_type_name"] == rig["suv_name"]  # from the saved car's own type
    assert out[0]["service_names"] == [rig["star_name"]]


@pytest.mark.asyncio
async def test_manager_dashboard_flagged_rows_name_the_car_and_the_wash(db, rig, cleanup):
    flagged = {
        "booking_number": f"BK-FLAG-{ObjectId()}", "customer_id": rig["customer"], "service_center_id": rig["center"],
        "status": "assigned", "issue_flag": "captain_delay", "service_ids": [rig["star"]], "vehicle_type": rig["hatch"],
        "vehicle_label": rig["hatch_name"], "total_amount": 349.0, "scheduled_date": now_ist().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0),
        "scheduled_slot": "17:00-20:00", "created_at": now_ist(), "is_deleted": False,
    }
    bid = (await db.bookings.insert_one(flagged)).inserted_id
    cleanup.insert(0, ("bookings", {"_id": bid}))
    s, e, ps, pe = resolve_period("today", None, None)
    data = await ManagerDashboardService(db).dashboard(rig["center"], s, e, ps, pe)
    row = next(i for i in data["ops"]["issues"] if i["booking_number"] == flagged["booking_number"])
    assert row["vehicle_type_name"] == rig["hatch_name"]
    assert row["service_names"] == [rig["star_name"]]
    assert row["slot_label"]  # existing fields intact


@pytest.mark.asyncio
async def test_collections_attention_rows_say_what_the_money_was_for(db, rig, cleanup):
    parked = (await db.payment_orders.insert_one({
        "purpose": "subscription", "status": "paid_attention", "resolved_at": None, "kind": "link", "customer_id": rig["customer"],
        "plan_id": rig["plan_id"], "vehicle_type": rig["hatch"], "service_id": rig["star"], "amount_paise": 139600,
        "attention_reason": "test", "flagged_at": now_ist(), "created_at": now_ist(),
    })).inserted_id
    cleanup.insert(0, ("payment_orders", {"_id": parked}))
    report = await PaymentService(db).admin_collections(None, None)
    row = next(a for a in report["attention"] if a["id"] == str(parked))
    plan_name = (await db.subscription_plans.find_one({"_id": ObjectId(rig["plan_id"])}))["name"]
    assert (row["plan_name"], row["vehicle_type_name"], row["service_name"]) == (plan_name, rig["hatch_name"], rig["star_name"])
    assert row["amount"] == 1396.0 and row["purpose"] == "subscription"
