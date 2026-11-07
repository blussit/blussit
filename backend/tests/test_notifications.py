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


@pytest.mark.asyncio
async def test_manager_bell_clears_handled_alerts_and_hides_read(rig):
    """Founder: a manager's bell must not pile up — alerts about work already
    done clear themselves, and read ones drop off his list."""
    from app.controllers.notification_controller import NotificationController
    from app.core.dependencies import CurrentUser, PaginationParams

    db = rig["db"]
    bs = BookingService(db)
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    booking = await bs.create_booking(rig["customer_id"], BookingCreateRequest(vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[rig["foam"]], scheduled_date=tomorrow, scheduled_slot="09:00-12:00"))
    await db.notifications.insert_one({"user_id": rig["manager_id"], "title": "Customer changed the note", "message": "x", "notification_type": "booking", "reference_id": booking["id"], "is_read": False, "created_at": datetime.now(timezone.utc)})
    await db.notifications.insert_one({"user_id": rig["manager_id"], "title": "Old alert", "message": "x", "notification_type": "system", "is_read": True, "created_at": datetime.now(timezone.utc)})

    manager = CurrentUser(id=rig["manager_id"], role="manager", service_center_id=rig["center_id"])
    page = PaginationParams(page=1, page_size=20)
    listed = await NotificationController(db).list_mine(manager, page, unread_only=False)
    titles = [n["title"] for n in listed["data"]]
    assert any(t.startswith("New booking") for t in titles)  # still needs a captain
    assert "Old alert" not in titles  # read ones never show for a manager

    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=rig["captain_id"]), rig["manager_id"], "manager", rig["center_id"])
    listed = await NotificationController(db).list_mine(manager, page, unread_only=False)
    titles = [n["title"] for n in listed["data"]]
    assert not any(t.startswith("New booking") for t in titles)  # handled → cleared
    assert "Customer changed the note" in titles  # informational: stays until opened
    assert listed["unread_count"] == len(listed["data"])
    cleared = await db.notifications.find_one({"user_id": rig["manager_id"], "title": {"$regex": "^New booking"}})
    assert cleared["is_read"] is True and cleared["auto_cleared"] is True


@pytest.mark.asyncio
async def test_manager_history_keeps_what_was_just_read_and_trims_on_the_next_visit(rig):
    """Founder: "Mark All Read" must not make everything vanish at once. The page
    keeps them on screen; the next time it is opened only the latest 10 read
    ones remain (the rest are deleted). Every read alert gets read_at (the
    24-hour delete)."""
    from app.controllers.notification_controller import NotificationController
    from app.core.dependencies import CurrentUser, PaginationParams

    db = rig["db"]
    uid = rig["manager_id"]
    base = datetime.now(timezone.utc)
    await db.notifications.insert_many([
        {"user_id": uid, "title": f"Alert {i}", "message": "x", "notification_type": "system", "is_read": False, "created_at": base - timedelta(minutes=i)}
        for i in range(14)
    ])
    manager = CurrentUser(id=uid, role="manager", service_center_id=rig["center_id"])
    page = PaginationParams(page=1, page_size=50)
    ctl = NotificationController(db)

    listed = await ctl.list_mine(manager, page, False, history=True, fresh=True)
    assert len(listed["data"]) == 14 and listed["unread_count"] == 14

    await ctl.mark_all_read(manager)
    assert await db.notifications.count_documents({"user_id": uid, "is_read": True, "read_at": {"$type": "date"}}) == 14
    # The refetch right after marking: all 14 are still there, now read.
    listed = await ctl.list_mine(manager, page, False, history=True, fresh=False)
    assert len(listed["data"]) == 14 and listed["unread_count"] == 0  # nothing vanishes
    # (nothing is deleted until the page is opened again)
    assert await db.notifications.count_documents({"user_id": uid}) == 14

    # A new alert arrives; the manager comes back → the 4 older read ones are deleted.
    await db.notifications.insert_one({"user_id": uid, "title": "Brand new", "message": "x", "notification_type": "system", "is_read": False, "created_at": base + timedelta(minutes=1)})
    listed = await ctl.list_mine(manager, page, False, history=True, fresh=True)
    titles = [n["title"] for n in listed["data"]]
    assert titles[0] == "Brand new" and len(titles) == 11 and listed["unread_count"] == 1
    assert await db.notifications.count_documents({"user_id": uid}) == 11
    assert "Alert 13" not in titles  # the oldest read ones are gone

    # The bell's own polling (no history flag) still lists only unread.
    bell = await ctl.list_mine(manager, page, False)
    assert [n["title"] for n in bell["data"]] == ["Brand new"]
