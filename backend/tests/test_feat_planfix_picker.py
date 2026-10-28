"""PLANS-AUDIT 2026-10-28 — the plan picker data. A customer with two plans
(a standard pass and a custom pass, on one car or two) must have BOTH
offered to the manager's Log A Done Job and to the customer's own booking,
each with what it covers, washes left, Last Booking Day, and — when it can't
be used — why. Local Mongo only."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import ManagerLogBookingRequest, QuickBookingLine
from app.services.booking_service import BookingService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests.test_feat_planfix_helpers import set_dates

pytestmark = pytest.mark.asyncio


async def _two_plans(db):
    s = await fb.rig(db)
    sv = await pf.services(db)
    cu = s["cu"]["id"]
    car2 = await pf.extra_car(db, cu)
    std = await pf.std_pass(db, cu, sv["star-wash"], car_id=s["cu"]["vehicle_id"])
    custom = await pf.custom_pass(db, s, car2, [(sv["deep-cleaning"], 2), (sv["star-wash"], 1)])
    return s, sv, car2, std, custom


async def test_staff_list_offers_every_plan_with_covers_validity_and_usability(db):
    s, sv, car2, std, custom = await _two_plans(db)
    rows = {r["id"]: r for r in await UserSubscriptionService(db).list_for_customer(s["cu"]["id"], "manager", s["center_id"])}
    assert set(rows) >= {std, custom}
    a, b = rows[std], rows[custom]
    assert a["usable"] and a["unusable_reason"] is None and a["auto_applies"] and not a["explicit_only"]
    assert [c["service_id"] for c in a["covers"]] == [sv["star-wash"]] and a["covers"][0]["remaining"] == 4
    assert a["registration_number"] and a["last_booking_day_label"]
    assert b["usable"] and b["explicit_only"] and not b["auto_applies"]
    assert {c["service_id"]: c["remaining"] for c in b["covers"]} == {sv["deep-cleaning"]: 2, sv["star-wash"]: 1}
    assert b["vehicle_id"] == car2 and b["registration_number"]


async def test_unusable_passes_say_why(db):
    s, sv, car2, std, custom = await _two_plans(db)
    subs = UserSubscriptionService(db)

    async def view(sub_id):
        return next(r for r in await subs.list_for_customer(s["cu"]["id"], "admin") if r["id"] == sub_id)

    await db.user_subscriptions.update_one({"_id": ObjectId(std)}, {"$set": {"remaining_service_count": 0, "status": "expired"}})
    v = await view(std)
    assert not v["usable"] and v["unusable_reason"] == "All washes used"
    await db.user_subscriptions.update_one({"_id": ObjectId(std)}, {"$set": {"remaining_service_count": 2, "status": "active"}})
    await set_dates(db, std, end=timedelta(days=-3))
    v = await view(std)
    assert not v["usable"] and v["unusable_reason"].startswith("Ended")
    # Extended: usable again, still says its Last Booking Day.
    await set_dates(db, std, end=timedelta(days=-3), extended=timedelta(days=4))
    v = await view(std)
    assert v["usable"] and v["in_extension"] and v["last_booking_day_label"]
    await db.vehicles.update_one({"_id": ObjectId(car2)}, {"$set": {"is_deleted": True}})
    v = await view(custom)
    assert not v["usable"] and v["unusable_reason"] == "Its car was removed"
    await db.vehicles.update_one({"_id": ObjectId(car2)}, {"$set": {"is_deleted": False, "vehicle_type": "x" * 24}})
    v = await view(custom)
    assert not v["usable"] and v["unusable_reason"] == "Its car type changed"


async def test_manager_log_can_choose_between_two_plans_of_one_customer(db):
    """The standard pass is picked by type (no choice needed); the custom
    pass only by naming car + pass. Both end up on their own booking."""
    s, sv, car2, std, custom = await _two_plans(db)
    cu = s["cu"]["id"]
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    hatch = await fb.get_hatchback_type_id(db)
    # Both cars are hatchbacks: the type line takes the standard pass; the
    # custom pass is named explicitly on its own car.
    when = (now_ist() - timedelta(days=1)).date().isoformat()
    await db.user_subscriptions.update_many({"customer_id": cu}, {"$set": {"start_date": now_ist() - timedelta(days=5)}})
    log = ManagerLogBookingRequest(
        customer_name="Plan Fix", customer_phone=customer["phone"], address_id=s["cu"]["address_id"], scheduled_date=when, service_time="10:00",
        lines=[
            QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"]], use_subscription=True),
            QuickBookingLine(vehicle_type=hatch, vehicle_id=car2, subscription_id=custom, service_ids=[sv["deep-cleaning"]], use_subscription=True),
        ],
    )
    out = await BookingService(db).create_manager_logged_visit(log, manager_id=s["mgr"]["id"], manager_center_id=s["center_id"])
    docs = [await fb.doc(db, b["id"]) for b in out["bookings"]]
    assert sorted(d["subscription_id"] for d in docs) == sorted([std, custom])
    assert (await pf.sub_doc(db, custom))["remaining_by_service"][sv["deep-cleaning"]] == 1
    assert (await pf.sub_doc(db, std))["remaining_service_count"] == 3


async def test_quote_names_the_pass_each_line_used(db):
    s, sv, car2, std, custom = await _two_plans(db)
    cu = s["cu"]["id"]
    customer = await db.users.find_one({"_id": ObjectId(cu)})
    hatch = await fb.get_hatchback_type_id(db)
    quote = await BookingService(db).quote_visit(
        customer_id=cu, phone=customer["phone"], scheduled_date=s["when"],
        lines=[QuickBookingLine(vehicle_type=hatch, service_ids=[sv["star-wash"]]),
               QuickBookingLine(vehicle_type=hatch, vehicle_id=car2, subscription_id=custom, service_ids=[sv["deep-cleaning"]])],
    )
    rows = quote["lines"]
    assert rows[0]["subscription_ids"] == [std] and rows[1]["subscription_ids"] == [custom]
    assert rows[0]["plan_covered"] == 1 and rows[1]["plan_covered"] == 1
