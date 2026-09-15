"""
Quick-booking model (2026-09): a booking from just a name + phone + what/
where/when — no vehicle record, no account up front, no OTP. Covers the
profile find-or-create, type-line expansion into a visit, the shared
4-digit service code, and the captain's code-based arrival check.
"""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import QuickAddress, QuickBookingLine, QuickBookingRequest, VerifyVehicleRequest
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_service_center


def _tomorrow() -> str:
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db, pincode="452077")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    hatchback = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    return {"db": db, "center_id": center_id, "hatchback": hatchback, "star": star}


def _quick(phone: str, lines: list[QuickBookingLine], **overrides) -> QuickBookingRequest:
    payload = dict(
        customer_name="Quick Tester",
        customer_phone=phone,
        address=QuickAddress(line1="12 Test Lane, Indore", pincode="452077"),
        lines=lines,
        scheduled_date=_tomorrow(),
        scheduled_slot="09:00-12:00",
    )
    payload.update(overrides)
    return QuickBookingRequest(**payload)


def _track(cleanup, phone: str):
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("addresses", {"line1": "12 Test Lane, Indore"}))
    cleanup.append(("bookings", {"customer_phone": phone}))
    cleanup.append(("booking_status_history", {"note": {"$regex": "Booking created"}}))


@pytest.mark.asyncio
async def test_quick_booking_creates_profile_address_and_booking(rig, cleanup):
    phone = "9777700001"
    _track(cleanup, phone)
    auth = AuthService(rig["db"])
    customer = await auth.ensure_customer_by_phone(phone, "Quick Tester")
    assert customer["role"] == "customer" and not customer.get("must_change_password")

    result = await BookingService(rig["db"]).create_quick_booking(
        _quick(phone, [QuickBookingLine(vehicle_type=rig["hatchback"], quantity=1, service_ids=[rig["star"]])]),
        customer=customer,
        source="app",
        allow_pinless=True,
    )
    assert result["vehicle_count"] == 1
    assert result["booking_group_id"] is None
    booking = result["bookings"][0]
    assert booking["vehicle_id"] is None
    assert booking["vehicle_type"] == rig["hatchback"]
    assert booking["vehicle_snapshot"]["label"]
    assert booking["status"] == "pending"
    assert result["service_code"] and len(result["service_code"]) == 4 and result["service_code"].isdigit()
    assert booking["service_code"] == result["service_code"]
    assert result["payment_link"] is None

    # The same phone is the same profile — a second booking never creates
    # a second account or a second copy of the address.
    again = await auth.ensure_customer_by_phone(phone, "Quick Tester")
    assert str(again["_id"]) == str(customer["_id"])
    assert await rig["db"].addresses.count_documents({"owner_id": str(customer["_id"]), "is_deleted": {"$ne": True}}) == 1


@pytest.mark.asyncio
async def test_two_of_a_type_become_a_visit_sharing_one_code(rig, cleanup):
    phone = "9777700002"
    _track(cleanup, phone)
    customer = await AuthService(rig["db"]).ensure_customer_by_phone(phone, "Two Cars")
    result = await BookingService(rig["db"]).create_quick_booking(
        _quick(phone, [QuickBookingLine(vehicle_type=rig["hatchback"], quantity=2, service_ids=[rig["star"]])]),
        customer=customer,
        source="app",
        allow_pinless=True,
    )
    assert result["vehicle_count"] == 2
    assert result["booking_group_id"]
    codes = {b["service_code"] for b in result["bookings"]}
    assert codes == {result["service_code"]}
    keys = {b["visit_line_key"] for b in result["bookings"]}
    assert len(keys) == 2  # two SUVs on one visit never collide on the unique index
    assert all(b["booking_group_id"] == result["booking_group_id"] for b in result["bookings"])


@pytest.mark.asyncio
async def test_captain_verifies_arrival_by_service_code(rig, cleanup):
    phone = "9777700003"
    _track(cleanup, phone)
    db = rig["db"]
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Code Check")
    bs = BookingService(db)
    result = await bs.create_quick_booking(
        _quick(phone, [QuickBookingLine(vehicle_type=rig["hatchback"], quantity=1, service_ids=[rig["star"]])]),
        customer=customer,
        source="app",
        allow_pinless=True,
    )
    booking_id = result["bookings"][0]["id"]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    # Put the job straight into the "on the way" state the check happens in.
    await db.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": {"status": "captain_on_the_way", "captain_id": captain_id}})

    wrong = "0000" if result["service_code"] != "0000" else "0001"
    with pytest.raises(BadRequestException):
        await bs.verify_vehicle(booking_id, VerifyVehicleRequest(service_code=wrong), captain_id)
    with pytest.raises(BadRequestException):
        # A plate means nothing on a type-only booking.
        await bs.verify_vehicle(booking_id, VerifyVehicleRequest(registration_number="MP09AB1234"), captain_id)
    verified = await bs.verify_vehicle(booking_id, VerifyVehicleRequest(service_code=result["service_code"]), captain_id)
    assert verified["vehicle_verified"] is True


@pytest.mark.asyncio
async def test_staff_phone_cannot_be_used_to_book(rig, cleanup):
    db = rig["db"]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    captain = await db.users.find_one({"_id": ObjectId(captain_id)})
    with pytest.raises(BadRequestException):
        await AuthService(db).ensure_customer_by_phone(captain["phone"], "Someone")


@pytest.mark.asyncio
async def test_one_code_verifies_every_car_on_the_visit(rig, cleanup):
    """A 2-car visit: the captain enters the visit's code once, on whichever
    car he tapped — both cars become verified, so he can start EITHER one
    first (the customer's call) and the other afterwards."""
    phone = "9777700004"
    _track(cleanup, phone)
    db = rig["db"]
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Two Cars Code")
    bs = BookingService(db)
    result = await bs.create_quick_booking(
        _quick(phone, [QuickBookingLine(vehicle_type=rig["hatchback"], quantity=2, service_ids=[rig["star"]])]),
        customer=customer,
        source="app",
        allow_pinless=True,
    )
    ids = [b["id"] for b in result["bookings"]]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    await db.bookings.update_many({"_id": {"$in": [ObjectId(i) for i in ids]}}, {"$set": {"status": "captain_on_the_way", "captain_id": captain_id}})

    await bs.verify_vehicle(ids[1], VerifyVehicleRequest(service_code=result["service_code"]), captain_id)
    docs = {str(d["_id"]): d for d in await db.bookings.find({"_id": {"$in": [ObjectId(i) for i in ids]}}).to_list(length=5)}
    assert all(docs[i]["vehicle_verified"] for i in ids)

    # Either car can be started now — pick the one he DIDN'T type the code on.
    from app.schemas.booking_schema import PhotoCaptureRequest

    started = await bs.capture_before_photo(ids[0], PhotoCaptureRequest(image_url="https://x/before.jpg", latitude=22.7, longitude=75.8), captain_id)
    assert started["status"] == "service_started"
    other = await db.bookings.find_one({"_id": ObjectId(ids[1])})
    assert other["status"] == "captain_on_the_way" and other["vehicle_verified"] is True
