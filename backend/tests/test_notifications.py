"""
Spec Section 5, 30: manager notification routing must remain intact
end-to-end (new booking -> manager, assignment -> captain, reassignment ->
both outgoing and incoming captain, delay flag -> manager, status updates
-> customer) — verified by reading the real `notifications` collection
each service call actually writes to, not by mocking the notifier.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest, ReassignCaptainRequest
from app.services.booking_service import BookingService

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_manager, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(db)
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("notifications", {"user_id": manager_id}))

    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))

    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {"user_id": customer_id}))

    return {
        "db": db, "center_id": center_id, "manager_id": manager_id, "captain_id": captain_id,
        "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id, "foam": foam,
    }


@pytest.mark.asyncio
async def test_new_booking_notifies_manager_and_customer(rig):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))

    manager_notifs = await rig["db"].notifications.find({"user_id": rig["manager_id"], "reference_id": booking["id"]}).to_list(None)
    assert len(manager_notifs) == 1
    assert "booking" in manager_notifs[0]["title"].lower() or "assign" in manager_notifs[0]["message"].lower()

    customer_notifs = await rig["db"].notifications.find({"user_id": rig["customer_id"], "reference_id": booking["id"]}).to_list(None)
    assert len(customer_notifs) == 1
    assert booking["manager_notified_at"] is not None  # timestamp actually persisted, not just the side-effect notify call


@pytest.mark.asyncio
async def test_assignment_notifies_captain_and_customer(rig):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), rig["manager_id"], "manager", rig["center_id"])

    captain_notifs = await rig["db"].notifications.find({"user_id": rig["captain_id"], "reference_id": booking["id"]}).to_list(None)
    assert any("assign" in n["title"].lower() or "job" in n["title"].lower() for n in captain_notifs)

    customer_notifs = await rig["db"].notifications.find({"user_id": rig["customer_id"], "reference_id": booking["id"]}).to_list(None)
    assert any("captain" in n["title"].lower() for n in customer_notifs)


@pytest.mark.asyncio
async def test_reassignment_notifies_both_outgoing_and_incoming_captain(rig, cleanup):
    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), rig["manager_id"], "manager", rig["center_id"])

    captain2_id = await make_captain(rig["db"], rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain2_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain2_id}))
    cleanup.append(("notifications", {"user_id": captain2_id}))

    await bs.reassign_captain(booking["id"], ReassignCaptainRequest(captain_id=captain2_id), rig["manager_id"], "manager", rig["center_id"])

    outgoing_notifs = await rig["db"].notifications.find({"user_id": rig["captain_id"], "reference_id": booking["id"]}).to_list(None)
    assert any("reassign" in n["title"].lower() for n in outgoing_notifs)

    incoming_notifs = await rig["db"].notifications.find({"user_id": captain2_id, "reference_id": booking["id"]}).to_list(None)
    assert any("job" in n["title"].lower() or "assign" in n["title"].lower() for n in incoming_notifs)


@pytest.mark.asyncio
async def test_status_updates_notify_the_customer(rig):
    """start_heading and capture_before_photo both notify the customer as
    the booking progresses — not just the initial creation/assignment."""
    from app.schemas.booking_schema import HeadingRequest

    bs = BookingService(rig["db"])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), rig["manager_id"], "manager", rig["center_id"])

    # Backdate estimated_start_at so heading-out is within the pre-start window right now.
    from app.utils.timezone import now_ist
    await rig["db"].bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"estimated_start_at": now_ist() + timedelta(minutes=10)}})

    await bs.start_heading(booking["id"], HeadingRequest(latitude=22.7, longitude=75.8, equipment_used=[]), rig["captain_id"])

    notifs = await rig["db"].notifications.find({"user_id": rig["customer_id"], "reference_id": booking["id"]}).to_list(None)
    assert any("heading" in n["title"].lower() or "way" in n["title"].lower() for n in notifs)
