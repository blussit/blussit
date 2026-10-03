"""
The 4-digit service code is the customer's proof that the captain is at the
right car. A captain must never be able to read it from his own app — not
in his job list, not on the booking/visit read, not in the response to his
own actions — or he could "verify" an arrival without meeting anyone. He
gets `requires_service_code` instead (code vs. legacy plate check). The
customer and the manager still see the code.
"""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.controllers.booking_controller import BookingController
from app.core.dependencies import CurrentUser, PaginationParams
from app.schemas.booking_schema import QuickAddress, QuickBookingLine, QuickBookingRequest, VerifyVehicleRequest
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_service_center


def _pg() -> PaginationParams:
    return PaginationParams(page=1, page_size=50, search=None, sort_by="created_at", sort_order=-1)


@pytest.fixture
async def visit(db, cleanup):
    center_id = await make_service_center(db, pincode="452077")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    phone = "9777700991"
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("addresses", {"line1": "7 Code Lane, Indore"}))
    cleanup.append(("bookings", {"customer_phone": phone}))
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Code Owner")
    hatchback = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    result = await BookingService(db).create_quick_booking(
        QuickBookingRequest(
            customer_name="Code Owner",
            customer_phone=phone,
            address=QuickAddress(line1="7 Code Lane, Indore", pincode="452077"),
            lines=[QuickBookingLine(vehicle_type=hatchback, quantity=2, service_ids=[star])],
            scheduled_date=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
            scheduled_slot="09:00-12:00",
        ),
        customer=customer,
        source="app",
        allow_pinless=True,
    )
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    ids = [b["id"] for b in result["bookings"]]
    await db.bookings.update_many(
        {"_id": {"$in": [ObjectId(i) for i in ids]}},
        {"$set": {"status": "captain_on_the_way", "captain_id": captain_id}},
    )
    return {
        "ids": ids,
        "group_id": result["booking_group_id"],
        "code": result["service_code"],
        "captain": CurrentUser(id=captain_id, role="captain"),
        "customer": CurrentUser(id=str(customer["_id"]), role="customer"),
        "manager": CurrentUser(id=str(ObjectId()), role="manager", service_center_id=center_id),
    }


def _no_code(booking: dict) -> None:
    assert "service_code" not in booking
    assert booking["requires_service_code"] is True


async def test_captain_reads_never_carry_the_code(db, visit):
    ctrl = BookingController(db)
    jobs = await ctrl.list_my_jobs(visit["captain"], None, _pg(), "active")
    assert {b["id"] for b in jobs["data"]} == set(visit["ids"])
    for b in jobs["data"]:
        _no_code(b)
    _no_code((await ctrl.get(visit["captain"], visit["ids"][0]))["data"])
    for b in (await ctrl.get_group(visit["captain"], visit["group_id"]))["data"]:
        _no_code(b)


async def test_captain_action_response_never_carries_the_code(db, visit):
    ctrl = BookingController(db)
    res = await ctrl.verify_vehicle(visit["captain"], visit["ids"][0], VerifyVehicleRequest(service_code=visit["code"]))
    assert res["data"]["vehicle_verified"] is True
    _no_code(res["data"])
    assert "platform_earning" not in res["data"]


async def test_customer_and_manager_still_see_the_code(db, visit):
    ctrl = BookingController(db)
    assert (await ctrl.get(visit["customer"], visit["ids"][0]))["data"]["service_code"] == visit["code"]
    assert (await ctrl.get(visit["manager"], visit["ids"][0]))["data"]["service_code"] == visit["code"]
