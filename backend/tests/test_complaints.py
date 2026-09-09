"""
Complaints — every complaint must be tied to one real booking the customer
actually owns (never a free-floating issue), which is also what routes it
deterministically to the manager of the right service center. Managers only
ever see their own center's complaints; replies form an append-only thread
rather than a single overwritten note.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import ForbiddenException, NotFoundException
from app.schemas.booking_schema import BookingCreateRequest
from app.schemas.complaint_schema import ComplaintCreateRequest
from app.services.booking_service import BookingService
from app.services.complaint_service import ComplaintService

from tests.factories import (
    get_star_wash_service_id,
    get_hatchback_type_id,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
)


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=10)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))

    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("complaints", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": manager_id}))

    bs = BookingService(db)
    target_date = datetime.now(timezone.utc) + timedelta(days=2)
    booking = await bs.create_booking(
        customer_id, BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[foam], scheduled_date=target_date, scheduled_slot="09:00-12:00")
    )
    return {"db": db, "center_id": center_id, "manager_id": manager_id, "customer_id": customer_id, "booking_id": booking["id"]}


@pytest.mark.asyncio
async def test_create_requires_the_customers_own_booking(rig, db, cleanup):
    cs = ComplaintService(db)
    other_customer_id, *_ = await make_customer_with_vehicle(db, await get_hatchback_type_id(db))
    cleanup.append(("users", {"_id": ObjectId(other_customer_id)}))
    with pytest.raises(ForbiddenException):
        await cs.create(other_customer_id, ComplaintCreateRequest(booking_id=rig["booking_id"], subject="Not my booking", description="Should be rejected"))


@pytest.mark.asyncio
async def test_create_rejects_a_nonexistent_booking(rig, db):
    cs = ComplaintService(db)
    with pytest.raises(NotFoundException):
        await cs.create(rig["customer_id"], ComplaintCreateRequest(booking_id=str(ObjectId()), subject="Ghost booking", description="Should 404"))


@pytest.mark.asyncio
async def test_create_routes_to_the_bookings_service_center_and_notifies_its_manager(rig):
    cs = ComplaintService(rig["db"])
    created = await cs.create(rig["customer_id"], ComplaintCreateRequest(booking_id=rig["booking_id"], subject="Late captain", description="Captain arrived 2 hours late"))
    assert created["service_center_id"] == rig["center_id"]
    assert created["status"] == "open"
    assert created["replies"] == []

    notif = await rig["db"].notifications.find_one({"user_id": rig["manager_id"], "reference_id": created["id"]})
    assert notif is not None


@pytest.mark.asyncio
async def test_manager_only_sees_their_own_centers_complaints(rig, db, cleanup):
    cs = ComplaintService(db)
    await cs.create(rig["customer_id"], ComplaintCreateRequest(booking_id=rig["booking_id"], subject="Issue", description="Some issue with the service"))

    other_center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(other_center_id)}))
    with pytest.raises(ForbiddenException):
        await cs.list_for_center(rig["center_id"], None, 1, 20, "manager", other_center_id)

    items, total = await cs.list_for_center(rig["center_id"], None, 1, 20, "manager", rig["center_id"])
    assert total == 1
    assert items[0]["customer_name"]  # enriched
    assert items[0]["booking_number"]  # enriched


@pytest.mark.asyncio
async def test_reply_appends_to_thread_and_can_change_status(rig):
    cs = ComplaintService(rig["db"])
    created = await cs.create(rig["customer_id"], ComplaintCreateRequest(booking_id=rig["booking_id"], subject="Damaged wheel", description="Wheel scratched during wash"))

    updated = await cs.add_reply(created["id"], rig["manager_id"], "manager", rig["center_id"], "Looked into it — captain confirms pre-existing scratch, sharing photo evidence.")
    assert len(updated["replies"]) == 1
    assert updated["replies"][0]["message"].startswith("Looked into it")
    assert updated["status"] == "open"  # unchanged, no status passed

    resolved = await cs.add_reply(created["id"], rig["manager_id"], "manager", rig["center_id"], "Confirmed pre-existing — closing this out.", status="resolved")
    assert len(resolved["replies"]) == 2
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == rig["manager_id"]


@pytest.mark.asyncio
async def test_manager_cannot_reply_to_another_centers_complaint(rig, db, cleanup):
    cs = ComplaintService(db)
    created = await cs.create(rig["customer_id"], ComplaintCreateRequest(booking_id=rig["booking_id"], subject="Issue", description="Some issue with the service"))

    other_center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(other_center_id)}))
    other_manager_id = await make_manager(db, other_center_id)
    cleanup.append(("users", {"_id": ObjectId(other_manager_id)}))

    with pytest.raises(ForbiddenException):
        await cs.add_reply(created["id"], other_manager_id, "manager", other_center_id, "Trying to butt in")
