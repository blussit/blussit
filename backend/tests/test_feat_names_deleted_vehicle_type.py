"""A deleted vehicle type never shows as a raw id.

Deleting a type is a SOFT delete (the doc stays as the tombstone). Types
removed before that rule are rebuilt by backfill_vehicle_type_tombstones.
Every view a historical record appears in resolves the ORIGINAL name; an
unknown id reads "Removed Car Type"; neither is ever a 24-hex id."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.services.vehicle_type_names import REMOVED_LABEL, VehicleTypeNames, backfill_vehicle_type_tombstones
from tests.factories import get_star_wash_service_id, make_address, make_customer, make_service_center, make_vehicle

HEX = re.compile(r"\b[0-9a-f]{24}\b")


def _display_ids(node, path=""):
    """Every *_name / *label field (what screens print) that is a raw id."""
    bad = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k.endswith("_name") or k.endswith("label")) and HEX.fullmatch(v):
                bad.append(f"{path}.{k}")
            bad += _display_ids(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad += _display_ids(v, f"{path}[{i}]")
    return bad


def _auth(user_id, role, center_id=None, tv=0):
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def admin_headers(db):
    admin = await db.users.find_one({"role": "admin"})
    return _auth(str(admin["_id"]), "admin", admin.get("service_center_id"), admin.get("token_version", 0))


async def _world(db, cleanup, admin_headers, name="XUV Names"):
    """A type with a finished booking, a (deleted) saved car and an expired pass."""
    async with _client() as c:
        res = await c.post("/api/v1/vehicle-types", json={"name": name, "display_order": 50}, headers=admin_headers)
        assert res.status_code == 200, res.text
    type_id = res.json()["data"]["id"]
    center = await make_service_center(db)
    customer = await make_customer(db)
    address = await make_address(db, customer)
    vehicle = await make_vehicle(db, customer, type_id)
    service_id = await get_star_wash_service_id(db)
    now = datetime.now(timezone.utc)
    booking = (await db.bookings.insert_one({
        "booking_number": f"NM{ObjectId()}"[-10:], "customer_id": customer, "vehicle_id": vehicle, "vehicle_type": type_id,
        "vehicle_label": name, "service_ids": [service_id], "service_center_id": center, "address_id": address,
        "status": "completed", "scheduled_date": datetime(2026, 1, 5), "scheduled_slot": "09:00-12:00",
        "total_amount": 299, "created_at": now, "updated_at": now, "is_deleted": False,
    })).inserted_id
    sub = (await db.user_subscriptions.insert_one({
        "customer_id": customer, "vehicle_type": type_id, "vehicle_id": vehicle, "service_id": service_id, "status": "expired",
        "service_center_id": center, "created_at": now, "is_deleted": False,
    })).inserted_id
    # The saved car is gone (soft-deleted) so the type is deletable.
    await db.vehicles.update_one({"_id": ObjectId(vehicle)}, {"$set": {"is_deleted": True}})
    cleanup.extend([
        ("bookings", {"_id": booking}), ("user_subscriptions", {"_id": sub}), ("vehicles", {"_id": ObjectId(vehicle)}),
        ("addresses", {"_id": ObjectId(address)}), ("users", {"_id": ObjectId(customer)}),
        ("service_centers", {"_id": ObjectId(center)}), ("vehicle_types", {"_id": ObjectId(type_id)}),
    ])
    return type_id, customer, center, str(booking)


@pytest.mark.asyncio
async def test_deleted_type_keeps_its_name_everywhere(db, cleanup, admin_headers):
    type_id, customer, center, booking_id = await _world(db, cleanup, admin_headers)
    async with _client() as c:
        res = await c.delete(f"/api/v1/vehicle-types/{type_id}", headers=admin_headers)
        assert res.status_code == 200, res.text
        # Soft delete: the doc (and name) is still there, flagged.
        doc = await db.vehicle_types.find_one({"_id": ObjectId(type_id)})
        assert doc["is_deleted"] is True and doc["name"] == "XUV Names" and doc["deleted_at"]
        VehicleTypeNames.invalidate()

        # Pickers no longer offer it; the lookup still resolves it, flagged.
        listed = (await c.get("/api/v1/vehicle-types")).json()["data"]
        assert type_id not in [t["id"] for t in listed]
        lookup = {t["id"]: t for t in (await c.get("/api/v1/vehicle-types/lookup")).json()["data"]}
        assert lookup[type_id]["name"] == "XUV Names" and lookup[type_id]["removed"] is True

        # Admin booking list + detail.
        res = await c.get("/api/v1/bookings", params={"page_size": 100}, headers=admin_headers)
        rows = [b for b in res.json()["data"] if b["id"] == booking_id]
        assert rows and rows[0]["vehicle_type_name"] == "XUV Names"
        detail = await c.get(f"/api/v1/bookings/{booking_id}", headers=admin_headers)
        assert detail.json()["data"]["vehicle_type_name"] == "XUV Names"

        # CRM 360 (bookings, plans) and the customer's own garage.
        res = await c.get(f"/api/v1/crm/customers/{customer}", headers=admin_headers)
        text = res.text
        assert res.status_code == 200 and "XUV Names" in text
        assert not _display_ids(res.json())
        garage = await c.get("/api/v1/vehicles/garage", headers=_auth(customer, "customer"))
        assert garage.status_code == 200
        assert not _display_ids(garage.json())

        # KPI breakdown by vehicle type.
        res = await c.get("/api/v1/analytics/vehicle-types", headers=admin_headers)
        row = [r for r in res.json()["data"] if r["vehicle_type_id"] == type_id]
        assert row and row[0]["vehicle_type_name"] == "XUV Names"


@pytest.mark.asyncio
async def test_hard_deleted_type_is_rebuilt_from_stored_labels(db, cleanup, admin_headers):
    type_id, customer, center, booking_id = await _world(db, cleanup, admin_headers, name="Pickup Names")
    await db.vehicle_types.delete_one({"_id": ObjectId(type_id)})  # the pre-soft-delete era
    VehicleTypeNames.invalidate()
    assert await VehicleTypeNames(db).name(type_id) == REMOVED_LABEL  # never the hash
    out = await backfill_vehicle_type_tombstones(db)
    assert out["created"] >= 1
    stub = await db.vehicle_types.find_one({"_id": ObjectId(type_id)})
    assert stub["name"] == "Pickup Names" and stub["is_deleted"] is True and stub["tombstone"] is True
    # Idempotent.
    again = await backfill_vehicle_type_tombstones(db)
    assert again["created"] == 0
    assert await db.vehicle_types.count_documents({"_id": ObjectId(type_id)}) == 1
    async with _client() as c:
        res = await c.get(f"/api/v1/crm/customers/{customer}", headers=admin_headers)
        assert "Pickup Names" in res.text


@pytest.mark.asyncio
async def test_unknown_type_without_any_stored_name_reads_removed_car_type(db, cleanup, admin_headers):
    ghost = str(ObjectId())
    center = await make_service_center(db)
    customer = await make_customer(db)
    cleanup.extend([("users", {"_id": ObjectId(customer)}), ("service_centers", {"_id": ObjectId(center)}), ("vehicle_types", {"_id": ObjectId(ghost)})])
    bid = (await db.bookings.insert_one({
        "booking_number": f"GH{ObjectId()}"[-10:], "customer_id": customer, "vehicle_type": ghost, "service_ids": [],
        "service_center_id": center, "status": "completed", "scheduled_date": datetime(2026, 1, 5), "scheduled_slot": "09:00-12:00",
        "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })).inserted_id
    cleanup.insert(0, ("bookings", {"_id": bid}))
    VehicleTypeNames.invalidate()
    await backfill_vehicle_type_tombstones(db)
    assert (await db.vehicle_types.find_one({"_id": ObjectId(ghost)}))["name"] == REMOVED_LABEL
    async with _client() as c:
        res = await c.get(f"/api/v1/crm/customers/{customer}", headers=admin_headers)
        assert REMOVED_LABEL in res.text and not _display_ids(res.json())


@pytest.mark.asyncio
async def test_rename_shows_the_current_name_for_live_types(db, cleanup, admin_headers):
    type_id, customer, center, booking_id = await _world(db, cleanup, admin_headers, name="Old Names")
    async with _client() as c:
        # a live type's old records show its CURRENT name (booking label is only the fallback)
        res = await c.put(f"/api/v1/vehicle-types/{type_id}", json={"name": "New Names"}, headers=admin_headers)
        assert res.status_code == 200
        lookup = {t["id"]: t for t in (await c.get("/api/v1/vehicle-types/lookup")).json()["data"]}
        assert lookup[type_id]["name"] == "New Names" and lookup[type_id]["removed"] is False
        res = await c.get(f"/api/v1/crm/customers/{customer}", headers=admin_headers)
        assert "New Names" in res.text
        # ...and once deleted it keeps the last name it had.
        assert (await c.delete(f"/api/v1/vehicle-types/{type_id}", headers=admin_headers)).status_code == 200
        lookup = {t["id"]: t for t in (await c.get("/api/v1/vehicle-types/lookup")).json()["data"]}
        assert lookup[type_id]["name"] == "New Names"


@pytest.mark.asyncio
async def test_type_in_use_is_still_refused_and_deleted_type_cannot_be_booked(db, cleanup, admin_headers):
    type_id, customer, center, booking_id = await _world(db, cleanup, admin_headers, name="Busy Names")
    # A live (undeleted) saved car blocks the delete exactly as before.
    live_car = await make_vehicle(db, customer, type_id)
    cleanup.insert(0, ("vehicles", {"_id": ObjectId(live_car)}))
    async with _client() as c:
        res = await c.delete(f"/api/v1/vehicle-types/{type_id}", headers=admin_headers)
        assert res.status_code in (400, 409), res.text
        assert (await db.vehicle_types.find_one({"_id": ObjectId(type_id)}))["is_deleted"] is False
        await db.vehicles.update_one({"_id": ObjectId(live_car)}, {"$set": {"is_deleted": True}})
        assert (await c.delete(f"/api/v1/vehicle-types/{type_id}", headers=admin_headers)).status_code == 200
        # The same name can be created again (slug freed).
        again = await c.post("/api/v1/vehicle-types", json={"name": "Busy Names"}, headers=admin_headers)
        assert again.status_code == 200, again.text
        cleanup.append(("vehicle_types", {"_id": ObjectId(again.json()["data"]["id"])}))
        # A new booking on the deleted type is refused.
        address = await make_address(db, customer)
        cleanup.append(("addresses", {"_id": ObjectId(address)}))
        res = await c.post("/api/v1/bookings", json={
            "vehicle_type": type_id, "address_id": address, "service_ids": [await get_star_wash_service_id(db)],
            "scheduled_date": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d"), "scheduled_slot": "09:00-12:00",
        }, headers=_auth(customer, "customer"))
        assert res.status_code == 400, res.text
