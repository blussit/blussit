"""PLANS-AUDIT 2026-10-28 — the overlap rule for plans assigned while another
is live (founder: "if admin or manager assigns me a new plan or custom plan,
it overlaps").

THE RULE: a car carries ONE live car-bound pass (standard, custom or
society — the `vehicle:` claim), and no two live passes may pay for the SAME
service on the same car type: a car-bound pass and a type pass (the
`type:` claim, no car) of the same vehicle type + service refuse each other
in both directions. A pass for a DIFFERENT service never overlaps, so a type
pass for Star Wash and a car pass for Deep Cleaning live side by side — and
the picker lists both. Local Mongo only."""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest
from app.schemas.subscription_schema import AssignSubscriptionRequest, SubscribeRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import UserSubscriptionService
from tests import test_feat_booking_helpers as fb
from tests import test_feat_planfix_helpers as pf
from tests.factories import make_subscription_plan

pytestmark = pytest.mark.asyncio


async def _rig(db):
    s = await fb.rig(db)
    sv = await pf.services(db)
    return s, sv, s["cu"]["id"], s["cu"]["vehicle_id"], await fb.get_hatchback_type_id(db)


async def test_car_pass_refused_while_a_type_pass_covers_the_same_service(db):
    s, sv, cu, car, hatch = await _rig(db)
    await pf.std_pass(db, cu, sv["star-wash"], vehicle_type=hatch)
    with pytest.raises(BadRequestException) as exc:
        await pf.std_pass(db, cu, sv["star-wash"], car_id=car)
    assert "already covered" in exc.value.message
    # validate_purchase (before money moves) says the same.
    plan = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[sv["star-wash"]])
    with pytest.raises(BadRequestException):
        await UserSubscriptionService(db).validate_purchase(cu, SubscribeRequest(plan_id=plan, vehicle_id=car, service_id=sv["star-wash"]))
    # A staff grant (assign) is refused too.
    with pytest.raises(BadRequestException):
        await UserSubscriptionService(db).assign(
            AssignSubscriptionRequest(customer_id=cu, plan_id=plan, vehicle_id=car, service_id=sv["star-wash"]), actor_center_id=s["center_id"])


async def test_a_second_car_of_the_type_lets_both_passes_live(db):
    """The type pass floats to the other hatchback, so nothing is paid twice:
    allowed once another car of the type is free (and SOC-4 stays allowed)."""
    s, sv, cu, car, hatch = await _rig(db)
    await pf.std_pass(db, cu, sv["star-wash"], car_id=car)
    assert await pf.std_pass(db, cu, sv["star-wash"], vehicle_type=hatch)  # a type pass is never refused for a car pass
    other = await pf.extra_car(db, cu)
    third = await pf.extra_car(db, cu)
    # Type jet pass; `other` may take a jet car pass only while `third` is free to use the type pass.
    await pf.std_pass(db, cu, sv["jet-wash"], vehicle_type=hatch)
    assert await pf.std_pass(db, cu, sv["jet-wash"], car_id=other)
    assert third


async def test_a_different_service_never_overlaps(db):
    s, sv, cu, car, hatch = await _rig(db)
    a = await pf.std_pass(db, cu, sv["star-wash"], vehicle_type=hatch)
    b = await pf.std_pass(db, cu, sv["jet-wash"], car_id=car)
    assert a != b
    rows = {r["id"]: r for r in await UserSubscriptionService(db).list_for_customer(cu, "admin")}
    assert rows[a]["usable"] and rows[b]["usable"]


async def test_custom_plan_refused_when_a_type_pass_covers_one_of_its_services(db):
    s, sv, cu, car, hatch = await _rig(db)
    await pf.std_pass(db, cu, sv["star-wash"], vehicle_type=hatch)
    await pf.known(db, s)
    mgr = {"actor_id": s["mgr"]["id"], "actor_role": "manager", "actor_center_id": s["center_id"]}
    cs = CustomPlanService(db)
    with pytest.raises(BadRequestException):
        await cs.create(CustomPlanCreateRequest(customer_id=cu, cars=[{"vehicle_id": car, "items": [
            {"service_id": sv["star-wash"], "count": 2}, {"service_id": sv["deep-cleaning"], "count": 1}]}], discount_amount=0), **mgr)
    # Deep Cleaning alone does not overlap Star Wash.
    cart = await cs.create(CustomPlanCreateRequest(customer_id=cu, cars=[{"vehicle_id": car, "items": [
        {"service_id": sv["deep-cleaning"], "count": 1}]}], discount_amount=0), **mgr)
    assert cart["id"]


async def test_one_live_car_pass_per_car_still_holds_for_every_kind(db):
    s, sv, cu, car, hatch = await _rig(db)
    await pf.std_pass(db, cu, sv["star-wash"], car_id=car)
    with pytest.raises(BadRequestException):
        await pf.std_pass(db, cu, sv["jet-wash"], car_id=car)
    await pf.known(db, s)
    mgr = {"actor_id": s["mgr"]["id"], "actor_role": "manager", "actor_center_id": s["center_id"]}
    with pytest.raises(BadRequestException):
        await CustomPlanService(db).create(CustomPlanCreateRequest(customer_id=cu, cars=[{"vehicle_id": car, "items": [
            {"service_id": sv["deep-cleaning"], "count": 1}]}], discount_amount=0), **mgr)
