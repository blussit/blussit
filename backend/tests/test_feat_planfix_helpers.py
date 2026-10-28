"""Shared rig for the test_feat_planfix_* suites (PLANS-AUDIT 2026-10-28:
two plans on one customer, extended passes on every path, the overlap rule).
No tests live here. Local Mongo only."""
import itertools
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.schemas.custom_plan_schema import CustomPlanCreateRequest
from app.schemas.subscription_schema import SubscribeRequest
from app.services.custom_plan_service import CustomPlanService
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import now_ist
from tests import test_feat_booking_helpers as fb
from tests.factories import make_subscription_plan, make_vehicle

_seq = itertools.count(1)


async def services(db) -> dict:
    return {x["slug"]: str(x["_id"]) for x in await db.services.find({"slug": {"$in": ["star-wash", "deep-cleaning", "exterior-polish", "jet-wash", "waterless-service"]}}).to_list(12)}


async def std_pass(db, customer_id: str, service_id: str, *, car_id: str | None = None, vehicle_type: str | None = None, washes: int = 4) -> str:
    """A standard monthly pass: car-bound when car_id is given, else a type pass."""
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[service_id], total_service_count=washes)
    body = SubscribeRequest(plan_id=plan_id, vehicle_id=car_id, service_id=service_id) if car_id else SubscribeRequest(
        plan_id=plan_id, vehicle_type=vehicle_type or await fb.get_hatchback_type_id(db), service_id=service_id)
    return (await UserSubscriptionService(db).subscribe(customer_id, body))["id"]


async def extra_car(db, customer_id: str, type_id: str | None = None) -> str:
    return await make_vehicle(db, customer_id, type_id or await fb.get_hatchback_type_id(db),
                              registration_number=f"MP09PF{next(_seq):04d}", is_default=False)


async def known(db, s: dict) -> None:
    """Make the rig's customer known to the manager's center (a past booking there)."""
    await db.bookings.insert_one({"customer_id": s["cu"]["id"], "service_center_id": s["center_id"], "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKPF{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})


async def custom_pass(db, s: dict, car_id: str, items: list[tuple[str, int]]) -> str:
    """A custom-plan pass for one car, sold by the center's manager for cash."""
    cu = s["cu"]["id"]
    await known(db, s)
    mgr = {"actor_id": s["mgr"]["id"], "actor_role": "manager", "actor_center_id": s["center_id"]}
    cs = CustomPlanService(db)
    cart = await cs.create(CustomPlanCreateRequest(
        customer_id=cu, cars=[{"vehicle_id": car_id, "items": [{"service_id": sid, "count": n} for sid, n in items]}], discount_amount=0), **mgr)
    out = await cs.mark_cash_paid(cart["id"], expected_revision=cart["revision"], note=None, **mgr)
    return out["subscription_ids"][0]


async def set_dates(db, sub_id: str, *, end: timedelta, extended: timedelta | None = None, start: timedelta | None = None) -> None:
    now = now_ist()
    fields = {"end_date": now + end}
    if extended is not None:
        fields["extended_until"] = now + extended
        fields["extension_days"] = max(1, int((extended - end).days))
    if start is not None:
        fields["start_date"] = now + start
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": fields})


async def sub_doc(db, sub_id: str) -> dict:
    return await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
