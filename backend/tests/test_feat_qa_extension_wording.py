"""QA 2026-10-07: extension wording (founder decision, PLANS-2).

- Every message and view shows a clear DATE: "Last Booking Day: 18 Oct
  2026" — the last day a wash can be booked on the pass (its extended end
  included), the same day every pass screen shows (`last_bookable_day`,
  human form `last_booking_day_label`).
- The extend success message: "Extended by 4 days — Last Booking Day:
  18 Oct 2026" (it once said "booked up to 2026-10-18", then "usable
  until 18 Oct 2026").
- The custom-plan "this car already has a live plan" refusal shows the same
  day, extension included (never the plan's original end).
Local Mongo only."""
import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import extension_message, pass_usable_until
from app.utils.timezone import from_stored, now_ist

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_customer, make_manager, make_service_center, make_vehicle
from tests.society_factories import auth, client

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture
async def rig(db, cleanup):
    center = await make_service_center(db)
    manager = await make_manager(db, center)
    customer = await make_customer(db)
    hatch = await get_hatchback_type_id(db)
    plate = f"MP09QW{next(_seq):04d}"
    car = await make_vehicle(db, customer, hatch, registration_number=plate)
    for uid in (manager, customer):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    for name in ("vehicles", "custom_plans", "user_subscriptions", "pass_claims", "bookings", "notifications", "payment_orders"):
        cleanup.append((name, {"customer_id": customer} if name != "vehicles" else {"owner_id": customer}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    await db.bookings.insert_one({"customer_id": customer, "service_center_id": center, "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKQW{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    return {"center": center, "manager": manager, "customer": customer, "car": car, "plate": plate,
            "star": await get_star_wash_service_id(db)}


def _mgr(rig) -> dict:
    return {"actor_role": "manager", "actor_center_id": rig["center"]}


async def _pass_ending_tomorrow(db, rig) -> str:
    service = CustomPlanService(db)
    cart = await service.create(
        CustomPlanCreateRequest(customer_id=rig["customer"], cars=[{"vehicle_id": rig["car"], "items": [{"service_id": rig["star"], "count": 3}]}]),
        actor_id=rig["manager"], **_mgr(rig),
    )
    result = await service.mark_cash_paid(cart["id"], expected_revision=1, note=None, actor_id=rig["manager"], **_mgr(rig))
    sub_id = result["subscription_ids"][0]
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"end_date": now_ist() + timedelta(days=1)}})
    return sub_id


def _day(dt) -> str:
    # "6 Nov 2026" — no leading zero (FINAL-POLISH 2026-10-08).
    return f"{dt.day} {dt.strftime('%b %Y')}"


async def test_extend_message_uses_human_usable_until_date(db, rig):
    sub_id = await _pass_ending_tomorrow(db, rig)
    async with client() as http:
        resp = await http.post(f"/api/v1/subscriptions/{sub_id}/extend", headers=auth(rig["manager"], "manager", rig["center"]), json={"days": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    last_day = from_stored(pass_usable_until(raw)).date() - timedelta(days=1)
    assert body["message"] == f"Extended by 4 days — Last Booking Day: {_day(last_day)}"
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body["message"]), "no ISO dates in a message people read"
    # The day shown is the one the data carries for every screen.
    assert body["data"]["last_bookable_day"] == last_day.isoformat()
    assert body["data"]["last_booking_day_label"] == _day(last_day)


async def test_extension_message_helper_singular_and_missing_date():
    assert extension_message(1, {"last_bookable_day": "2026-10-19"}) == "Extended by 1 day — Last Booking Day: 19 Oct 2026"
    assert extension_message(2, {}) == "Extended by 2 days"


async def test_live_pass_refusal_shows_the_extended_date(db, rig):
    sub_id = await _pass_ending_tomorrow(db, rig)
    from app.services.subscription_service import UserSubscriptionService

    await UserSubscriptionService(db).extend_pass(sub_id, 6, note=None, actor_id=rig["manager"], **_mgr(rig))
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    end_day = from_stored(raw["end_date"]).date() - timedelta(days=1)
    usable_day = from_stored(pass_usable_until(raw)).date() - timedelta(days=1)
    assert usable_day > end_day
    with pytest.raises(BadRequestException) as exc:
        await CustomPlanService(db).create(
            CustomPlanCreateRequest(customer_id=rig["customer"], cars=[{"vehicle_id": rig["car"], "items": [{"service_id": rig["star"], "count": 2}]}]),
            actor_id=rig["manager"], **_mgr(rig),
        )
    assert f"Last Booking Day: {_day(usable_day)}" in exc.value.message
    assert _day(from_stored(raw["end_date"])) not in exc.value.message


async def test_pass_views_carry_the_last_booking_day_label(db, rig):
    sub_id = await _pass_ending_tomorrow(db, rig)
    from app.services.subscription_service import UserSubscriptionService

    view = await UserSubscriptionService(db).extend_pass(sub_id, 2, note=None, actor_id=rig["manager"], **_mgr(rig))
    raw = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    last_day = from_stored(pass_usable_until(raw)).date() - timedelta(days=1)
    assert view["last_booking_day_label"] == _day(last_day)
    mine = await UserSubscriptionService(db).list_my_subscriptions(rig["customer"])
    assert next(s for s in mine if s["id"] == sub_id)["last_booking_day_label"] == _day(last_day)
    # The custom-plan cart view's pass block carries it too.
    service = CustomPlanService(db)
    cart = await service.get(raw["custom_plan_id"])
    car = (await service.view(cart))["cars"][0]
    assert car["subscription"]["last_booking_day_label"] == _day(last_day)
