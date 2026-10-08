"""Core fix round 1 — centers, hours and indexes (audit 2026-10-07: ADM-07,
ADM-05, BIZ-02 opening hours, DEP-08)."""
import os

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


def _center_body(**over) -> dict:
    body = {
        "name": "Hours Test Center",
        "location": {"address": "1 Test Road", "city": "Indore", "state": "MP", "pincode": "452777", "latitude": 22.7, "longitude": 75.8},
    }
    body.update(over)
    return body


# ---------------------------------------------------------------- hours (founder: 7 AM – 7 PM)


async def test_opening_hours_defaults_are_7am_to_7pm():
    from app.models.service_center import ServiceCenterLocation, ServiceCenterModel
    from app.schemas.service_center_schema import ServiceCenterCreateRequest
    from app.services.booking_policy_service import DEFAULT_BOOKING_POLICY

    req = ServiceCenterCreateRequest(**_center_body())
    assert (req.working_hours_start, req.working_hours_end) == ("07:00", "19:00")
    model = ServiceCenterModel(name="x", code="X-1", location=ServiceCenterLocation(**_center_body()["location"]))
    assert (model.working_hours_start, model.working_hours_end) == ("07:00", "19:00")
    assert (DEFAULT_BOOKING_POLICY["operating_start"], DEFAULT_BOOKING_POLICY["operating_end"]) == ("07:00", "19:00")


# ---------------------------------------------------------------- ADM-07


@pytest.mark.parametrize("hours", [("9am", "19:00"), ("09:00", "7pm"), ("25:00", "26:00"), ("9:00", "19:00"), ("19:00", "09:00"), ("10:00", "10:00")])
async def test_adm07_invalid_working_hours_refused_on_create(db, hours):
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.post("/api/v1/service-centers", json=_center_body(working_hours_start=hours[0], working_hours_end=hours[1]), headers=adm["h"])
    assert r.status_code == 422, r.text


async def test_adm07_update_validated_against_stored_hours(db):
    cid, pin = await h.center(db)  # 09:00 – 19:00
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"working_hours_end": "8pm"}, headers=adm["h"])
        assert r.status_code == 422
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"working_hours_end": "08:00"}, headers=adm["h"])
        assert r.status_code == 400, r.text
        r = await c.put(f"/api/v1/service-centers/{cid}", json={"slot_duration_minutes": 0}, headers=adm["h"])
        assert r.status_code == 422
        r = await c.get(f"/api/v1/service-centers/{cid}/slots", params={"date": h.day(2)})
        assert r.status_code == 200


# ---------------------------------------------------------------- ADM-05


async def test_adm05_center_with_live_booking_cannot_be_deleted(db):
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    adm = await h.admin(db)
    when, keys = await h.slot_keys(db, cid)
    async with h.client() as c:
        assert (await h.book(c, db, cu, when, keys[0])).status_code == 200
        r = await c.delete(f"/api/v1/service-centers/{cid}", headers=adm["h"])
        assert r.status_code == 400, r.text
        assert "deactivate" in r.json()["message"].lower()
    assert (await db.service_centers.find_one({"_id": ObjectId(cid)}))["is_deleted"] is False


async def test_adm05_center_with_a_captain_cannot_be_deleted(db):
    cid, pin = await h.center(db)
    await h.captain(db, cid)
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.delete(f"/api/v1/service-centers/{cid}", headers=adm["h"])
    assert r.status_code == 400 and "captain" in r.json()["message"].lower(), r.text


async def test_adm05_unreferenced_center_can_be_deleted(db):
    cid, pin = await h.center(db)
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.delete(f"/api/v1/service-centers/{cid}", headers=adm["h"])
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- DEP-08


async def test_dep08_missing_critical_indexes_is_empty_after_boot(db):
    from app.core.database import missing_critical_indexes

    assert await missing_critical_indexes() == []


async def test_dep08_v3_built_before_legacy_dropped_and_missing_reported(db, monkeypatch):
    """On a scratch database: a legacy v2 index plus duplicate rows that
    violate v3. Boot must keep v2 (never leave the collection unguarded),
    and the readiness check must name the missing v3."""
    from app.core import database
    from app.core.database import create_indexes, missing_critical_indexes

    name = f"fix_core_idx_{os.getpid()}"
    client = AsyncIOMotorClient(settings.MONGO_URI)
    scratch = client[name]
    try:
        keys = [("customer_id", 1), ("visit_line_key", 1), ("scheduled_date", 1), ("scheduled_slot", 1)]
        await scratch.bookings.create_index(keys, unique=True, name="uniq_active_customer_slot_v2", partialFilterExpression={"status": {"$in": ["pending"]}})
        row = {"customer_id": "c1", "visit_line_key": "t#0", "scheduled_date": "d", "scheduled_slot": "s", "status": "assigned", "is_deleted": False}
        await scratch.bookings.insert_many([dict(row), dict(row)])
        monkeypatch.setattr(database.mongodb, "db", scratch)
        await create_indexes()
        names = {i["name"] for i in await scratch.bookings.list_indexes().to_list(None)}
        assert "uniq_active_customer_slot_v2" in names and "uniq_active_customer_slot_v3" not in names
        missing = await missing_critical_indexes(scratch)
        assert any("uniq_active_customer_slot_v3" in m for m in missing), missing
        # Clean the duplicate: the next boot builds v3 and only then drops v2.
        await scratch.bookings.delete_one({"status": "assigned"})
        await create_indexes()
        names = {i["name"] for i in await scratch.bookings.list_indexes().to_list(None)}
        assert "uniq_active_customer_slot_v3" in names and "uniq_active_customer_slot_v2" not in names
        assert await missing_critical_indexes(scratch) == []
    finally:
        await client.drop_database(name)
        client.close()
