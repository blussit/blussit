"""QA 2026-10-07: the custom-plan PREVIEW and CREATE must agree.

- Preview takes the same customer identification as create: `customer_id`,
  or `customer_phone` (+ name) — an existing account found by phone (never
  created by a preview), or a new number (plate + type cars only).
- A saved `vehicle_id` prices only when the customer is resolved AND known
  to the actor's center — in preview and in create alike.
- A car already on a live pass is refused by both, with the same words
  (also when it's named by plate rather than by saved car).
Local Mongo only."""
import itertools
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import AppException
from app.schemas.custom_plan_schema import CustomPlanCreateRequest, CustomPlanPreviewRequest
from app.services.custom_plan_service import CustomPlanService

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    get_suv_type_id,
    make_customer,
    make_manager,
    make_service_center,
    make_vehicle,
)

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture
async def rig(db, cleanup):
    center = await make_service_center(db)
    other = await make_service_center(db)
    manager = await make_manager(db, center)
    customer = await make_customer(db, name="Qa Customer")
    phone = (await db.users.find_one({"_id": ObjectId(customer)}))["phone"]
    hatch = await get_hatchback_type_id(db)
    plate = f"MP09QA{next(_seq):04d}"
    car = await make_vehicle(db, customer, hatch, registration_number=plate)
    star = await get_star_wash_service_id(db)
    for uid in (manager, customer):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("vehicles", {"owner_id": customer}))
    cleanup.append(("custom_plans", {"customer_id": customer}))
    cleanup.append(("user_subscriptions", {"customer_id": customer}))
    cleanup.append(("pass_claims", {"customer_id": customer}))
    cleanup.append(("bookings", {"customer_id": customer}))
    # Cash rows a test leaves behind would count as plan revenue in later KPI suites.
    cleanup.append(("payment_orders", {"customer_id": customer}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    # The customer is known to `center` (a past booking there).
    await db.bookings.insert_one({"customer_id": customer, "service_center_id": center, "status": "completed", "is_deleted": False,
                                  "booking_number": f"BKQA{next(_seq):05d}", "created_at": datetime.now(timezone.utc)})
    return {"center": center, "other": other, "manager": manager, "customer": customer, "phone": phone,
            "hatch": hatch, "car": car, "plate": plate, "star": star}


def _mgr(rig, center: str | None = None) -> dict:
    return {"actor_role": "manager", "actor_center_id": center or rig["center"]}


async def _both(db, rig, body: dict, center: str | None = None):
    """(preview result or error, create result or error) for the same body."""
    service = CustomPlanService(db)
    out = []
    try:
        out.append(await service.preview(CustomPlanPreviewRequest(**body), **_mgr(rig, center)))
    except AppException as exc:
        out.append(exc)
    try:
        out.append(await service.create(CustomPlanCreateRequest(**body), actor_id=rig["manager"], **_mgr(rig, center)))
    except AppException as exc:
        out.append(exc)
    return out


def _items(rig, n: int = 3) -> list[dict]:
    return [{"service_id": rig["star"], "count": n}]


async def test_preview_accepts_phone_and_saved_car_like_create(db, rig):
    body = {"customer_phone": rig["phone"], "customer_name": "Qa Customer", "cars": [{"vehicle_id": rig["car"], "items": _items(rig)}]}
    preview, created = await _both(db, rig, body)
    assert not isinstance(preview, AppException), preview
    assert not isinstance(created, AppException), created
    assert preview["total_amount"] == created["total_amount"] > 0
    assert preview["cars"][0]["vehicle_id"] == rig["car"]


async def test_preview_new_number_prices_plate_cars_and_creates_nothing(db, rig, cleanup):
    phone = f"7{next(_seq):09d}"[:10]
    body = {"customer_phone": phone, "customer_name": "Brand New", "cars": [{"vehicle_type": rig["hatch"], "registration_number": "MP09NEW1", "items": _items(rig)}]}
    service = CustomPlanService(db)
    preview = await service.preview(CustomPlanPreviewRequest(**body), **_mgr(rig))
    assert preview["total_amount"] > 0
    assert await db.users.find_one({"phone": phone}) is None, "a preview never creates the account"
    # …and a saved car can't be named for a number nobody owns yet.
    with pytest.raises(AppException):
        await service.preview(CustomPlanPreviewRequest(customer_phone=phone, cars=[{"vehicle_id": rig["car"], "items": _items(rig)}]), **_mgr(rig))


async def test_out_of_scope_customer_saved_car_refused_by_both(db, rig):
    """A manager of ANOTHER center can't name this customer's saved car."""
    other_manager = await make_manager(db, rig["other"])
    await db.users.delete_one({"_id": ObjectId(other_manager)})  # the actor id is all that's needed
    body = {"customer_phone": rig["phone"], "customer_name": "Qa Customer", "cars": [{"vehicle_id": rig["car"], "items": _items(rig)}]}
    preview, created = await _both(db, rig, body, center=rig["other"])
    assert isinstance(preview, AppException) and isinstance(created, AppException)
    assert preview.status_code == created.status_code == 404
    assert preview.message == created.message


async def test_live_pass_refused_by_both_with_the_same_words(db, rig):
    service = CustomPlanService(db)
    cart = await service.create(
        CustomPlanCreateRequest(customer_id=rig["customer"], cars=[{"vehicle_id": rig["car"], "items": _items(rig)}]),
        actor_id=rig["manager"], **_mgr(rig),
    )
    await service.mark_cash_paid(cart["id"], expected_revision=1, note=None, actor_id=rig["manager"], **_mgr(rig))

    # Named by saved car (id or phone) …
    for body in (
        {"customer_id": rig["customer"], "cars": [{"vehicle_id": rig["car"], "items": _items(rig)}]},
        {"customer_phone": rig["phone"], "customer_name": "Qa Customer", "cars": [{"vehicle_id": rig["car"], "items": _items(rig)}]},
        # … or by its plate + type: create resolves the plate to that car.
        {"customer_phone": rig["phone"], "customer_name": "Qa Customer", "cars": [{"vehicle_type": rig["hatch"], "registration_number": rig["plate"], "items": _items(rig)}]},
    ):
        preview, created = await _both(db, rig, body)
        assert isinstance(preview, AppException), f"preview priced a car on a live pass: {body}"
        assert isinstance(created, AppException)
        assert preview.status_code == created.status_code == 400
        assert preview.message == created.message
        assert "already has an active plan" in preview.message and rig["plate"] in preview.message


async def test_plate_saved_as_another_type_refused_by_both(db, rig):
    body = {"customer_phone": rig["phone"], "customer_name": "Qa Customer",
            "cars": [{"vehicle_type": await get_suv_type_id(db), "registration_number": rig["plate"], "items": _items(rig)}]}
    preview, created = await _both(db, rig, body)
    assert isinstance(preview, AppException) and isinstance(created, AppException)
    assert preview.message == created.message
