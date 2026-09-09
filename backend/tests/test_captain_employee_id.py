"""
Captain employee ids ("CAP-001"): auto-assigned at creation via an atomic
counter, backfilled at startup for older captains, and surfaced to the
CUSTOMER as part of the captain's public card on an enriched booking
(photo + staff id + phone — who's coming to your door).
"""
import pytest
from bson import ObjectId

from app.models.enums import UserRole
from app.schemas.user_schema import StaffCreateRequest
from app.services.auth_service import AuthService, assign_missing_employee_ids
from app.services.booking_service import BookingService

from tests.factories import get_hatchback_type_id, make_captain, make_customer_with_vehicle, make_service_center


@pytest.mark.asyncio
async def test_new_captains_get_sequential_unique_ids(db, cleanup):
    svc = AuthService(db)
    a = await svc.create_staff_account(
        StaffCreateRequest(full_name="Cap One", email="cap.one@test.example", phone="9811110001", password="Password@1", role=UserRole.CAPTAIN),
        created_by="admin", creator_role="admin",
    )
    b = await svc.create_staff_account(
        StaffCreateRequest(full_name="Cap Two", email="cap.two@test.example", phone="9811110002", password="Password@1", role=UserRole.CAPTAIN),
        created_by="admin", creator_role="admin",
    )
    cleanup.append(("users", {"_id": {"$in": [ObjectId(a["id"]), ObjectId(b["id"])]}}))
    assert a["employee_id"].startswith("CAP-") and b["employee_id"].startswith("CAP-")
    assert a["employee_id"] != b["employee_id"]

    # Managers don't carry one.
    m = await svc.create_staff_account(
        StaffCreateRequest(full_name="Mgr One", email="mgr.one@test.example", password="Password@1", role=UserRole.MANAGER),
        created_by="admin", creator_role="admin",
    )
    cleanup.append(("users", {"_id": ObjectId(m["id"])}))
    assert m.get("employee_id") is None


@pytest.mark.asyncio
async def test_startup_backfill_assigns_missing_ids(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)  # factory sets no employee_id
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))

    await assign_missing_employee_ids(db)
    doc = await db.users.find_one({"_id": ObjectId(captain_id)})
    assert doc["employee_id"].startswith("CAP-")

    # Idempotent — a second run never reassigns.
    first = doc["employee_id"]
    await assign_missing_employee_ids(db)
    doc = await db.users.find_one({"_id": ObjectId(captain_id)})
    assert doc["employee_id"] == first


@pytest.mark.asyncio
async def test_enriched_booking_carries_captain_public_card(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    await db.users.update_one(
        {"_id": ObjectId(captain_id)},
        {"$set": {"employee_id": "CAP-777", "captain_kyc": {"status": "verified", "photo_url": "/uploads/cap.jpg"}}},
    )
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))

    bs = BookingService(db)
    raw = {
        "_id": ObjectId(),
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "address_id": address_id,
        "captain_id": captain_id,
        "service_ids": [],
    }
    enriched = (await bs._enrich_bookings([raw]))[0]
    card = enriched["captain_profile"]
    assert card["employee_id"] == "CAP-777"
    assert card["photo_url"] == "/uploads/cap.jpg"
    assert card["verified"] is True
    assert card["phone"]  # the customer can call
    # Only the five public fields — nothing else from the captain doc leaks.
    assert set(card.keys()) == {"full_name", "phone", "employee_id", "photo_url", "verified"}


@pytest.mark.asyncio
async def test_unassigned_booking_has_no_captain_card(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    bs = BookingService(db)
    raw = {"_id": ObjectId(), "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id, "captain_id": None, "service_ids": []}
    enriched = (await bs._enrich_bookings([raw]))[0]
    assert enriched["captain_profile"] is None
