"""Customer location on the users doc: derivation precedence, update hooks,
backfill (idempotent / bounded / resumable), staff filters + sorting + index
use, manager scoping unchanged, and coordinate privacy."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.services.customer_location_service import (
    backfill_customer_locations, note_booking_created, refresh_customer_location, set_manual_location,
)
from tests.factories import make_customer, make_manager, make_service_center

BLR = dict(pincode="560101", latitude=12.97, longitude=77.59)
PUNE = dict(pincode="411101", latitude=18.52, longitude=73.85)


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


async def _addr(db, owner, *, pincode, city="Bengaluru", lat=None, lng=None, default=True):
    return str((await db.addresses.insert_one({
        "owner_id": owner, "label": "Home", "line1": "1 Test Rd", "city": city, "state": "KA", "pincode": pincode,
        "latitude": lat, "longitude": lng, "is_default": default, "is_deleted": False, "created_at": datetime.now(timezone.utc),
    })).inserted_id)


async def _booking(db, customer, center, **extra):
    doc = {"booking_number": f"LC{ObjectId()}"[-10:], "customer_id": customer, "service_center_id": center, "status": "completed",
           "created_at": datetime.now(timezone.utc), "is_deleted": False,
           "scheduled_date": datetime(2026, 1, 5), "scheduled_slot": "09:00-12:00", **extra}
    doc["_id"] = (await db.bookings.insert_one(doc)).inserted_id
    return doc


@pytest.fixture
async def world(db, cleanup):
    blr = await make_service_center(db, name="LocCtr Blr", **BLR)
    pune = await make_service_center(db, name="LocCtr Pune", **PUNE)
    cleanup.extend([("service_centers", {"_id": {"$in": [ObjectId(blr), ObjectId(pune)]}})])
    return blr, pune


async def _customer(db, cleanup):
    cid = await make_customer(db)
    cleanup.extend([("users", {"_id": ObjectId(cid)}), ("addresses", {"owner_id": cid}), ("bookings", {"customer_id": cid})])
    return cid


@pytest.mark.asyncio
async def test_precedence_address_then_booking_then_none(db, cleanup, world):
    blr, pune = world
    cid = await _customer(db, cleanup)
    assert (await refresh_customer_location(db, cid))["home_center_id"] is None  # nothing known
    # booking only -> the booking's center
    await _booking(db, cid, pune, address_snapshot={"city": "Pune", "state": "MH", "pincode": "411101"})
    d = await refresh_customer_location(db, cid)
    assert (d["home_center_id"], d["location_source"], d["home_city"], d["bookings_count"]) == (pune, "booking", "Pune", 1)
    # a covered default address beats the booking
    await _addr(db, cid, pincode="560101", lat=12.97, lng=77.59)
    d = await refresh_customer_location(db, cid)
    assert (d["home_center_id"], d["location_source"], d["home_city"], d["home_pincode"], d["home_lat"]) == (blr, "address", "Bengaluru", "560101", 12.97)
    assert d["last_booking_center_id"] == pune and d["first_booking_center_id"] == pune
    # an address nobody covers falls back to the booking's center
    await db.addresses.delete_many({"owner_id": cid})
    await _addr(db, cid, pincode="999999", city="Nowhere")
    d = await refresh_customer_location(db, cid)
    assert (d["home_center_id"], d["location_source"]) == (pune, "booking")
    # pincode-only address is covered by its center's service_pincodes
    await db.addresses.delete_many({"owner_id": cid})
    await _addr(db, cid, pincode="560101")
    assert (await refresh_customer_location(db, cid))["home_center_id"] == blr
    # manual wins and survives refreshes (counters still move)
    await set_manual_location(db, cid, pune)
    await refresh_customer_location(db, cid)
    d = await db.users.find_one({"_id": ObjectId(cid)})
    assert (d["home_center_id"], d["location_source"], d["bookings_count"]) == (pune, "manual", 1)


@pytest.mark.asyncio
async def test_address_api_and_booking_hooks_keep_it_current(db, cleanup, world):
    blr, pune = world
    cid = await _customer(db, cleanup)
    async with _client() as c:
        h = _auth(cid, "customer")
        res = await c.post("/api/v1/addresses", headers=h, json={
            "line1": "9 MG Road", "city": "Bengaluru", "state": "KA", "pincode": "560101", "latitude": 12.97, "longitude": 77.59, "is_default": True})
        assert res.status_code == 200, res.text
        aid = res.json()["data"]["id"]
        user = await db.users.find_one({"_id": ObjectId(cid)})
        assert (user["home_center_id"], user["location_source"]) == (blr, "address")
        res = await c.put(f"/api/v1/addresses/{aid}", headers=h, json={"pincode": "411101", "latitude": 18.52, "longitude": 73.85, "city": "Pune"})
        assert res.status_code == 200, res.text
        user = await db.users.find_one({"_id": ObjectId(cid)})
        assert (user["home_center_id"], user["home_city"]) == (pune, "Pune")
        # booking hook: counters + first/last
        b1 = await _booking(db, cid, blr)
        await note_booking_created(db, b1)
        b2 = await _booking(db, cid, pune)
        await note_booking_created(db, b2)
        user = await db.users.find_one({"_id": ObjectId(cid)})
        assert (user["bookings_count"], user["first_booking_center_id"], user["last_booking_center_id"]) == (2, blr, pune)
        # removing the only address falls back to the latest booking's center
        assert (await c.delete(f"/api/v1/addresses/{aid}", headers=h)).status_code == 200
        user = await db.users.find_one({"_id": ObjectId(cid)})
        assert (user["home_center_id"], user["location_source"]) == (pune, "booking")


@pytest.mark.asyncio
async def test_backfill_is_idempotent_bounded_and_resumable(db, cleanup, world):
    blr, pune = world
    ids = []
    for i in range(5):
        cid = await _customer(db, cleanup)
        await _addr(db, cid, pincode="560101")
        ids.append(cid)
    # Bounded: 2 per batch and a zero budget after the first pass processes only one batch.
    await db.users.update_many({"role": "customer", "_id": {"$nin": [ObjectId(i) for i in ids]}}, {"$set": {"location_updated_at": datetime.now(timezone.utc)}})
    first = await backfill_customer_locations(db, batch_size=2, budget_seconds=0.0001)
    assert first["processed"] <= 2 and first["done"] is False
    stamped = lambda: db.users.count_documents({"_id": {"$in": [ObjectId(i) for i in ids]}, "location_updated_at": {"$exists": True}})
    assert await stamped() >= 1
    # Resumable: keep going until done; every one of ours is stamped and correct.
    for _ in range(50):
        if (await backfill_customer_locations(db, batch_size=2, budget_seconds=5))["done"]:
            break
    assert await stamped() == 5
    for i in ids:
        u = await db.users.find_one({"_id": ObjectId(i)})
        assert (u["home_center_id"], u["bookings_count"]) == (blr, 0)
    # Idempotent: a second pass touches nothing.
    again = await backfill_customer_locations(db, batch_size=2, budget_seconds=5)
    assert again == {"processed": 0, "done": True, "last_id": None}
    # --all re-derives and resumes by cursor.
    r = await backfill_customer_locations(db, batch_size=2, budget_seconds=5, all_customers=True)
    assert r["done"] is True and r["processed"] >= 5
    # Staff accounts are never given a location.
    staff = await db.users.find_one({"role": "admin"})
    assert "home_center_id" not in staff


@pytest.mark.asyncio
async def test_admin_filters_sorting_and_index_use(db, cleanup, world, admin_headers):
    blr, pune = world
    mk = {}
    for name, pin, city in (("Aaa", "560101", "Bengaluru"), ("Bbb", "411101", "Pune"), ("Ccc", "560101", "Bengaluru")):
        cid = await _customer(db, cleanup)
        await db.users.update_one({"_id": ObjectId(cid)}, {"$set": {"full_name": f"Loc {name}"}})
        await _addr(db, cid, pincode=pin, city=city)
        await refresh_customer_location(db, cid)
        mk[name] = cid
    async with _client() as c:
        res = await c.get("/api/v1/users", params={"role": "customer", "center_id": blr, "page_size": 100}, headers=admin_headers)
        names = {u["full_name"] for u in res.json()["data"]}
        assert {"Loc Aaa", "Loc Ccc"} <= names and "Loc Bbb" not in names
        row = next(u for u in res.json()["data"] if u["full_name"] == "Loc Aaa")
        assert row["home_center_name"] == "LocCtr Blr" and row["home_city"] == "Bengaluru" and "home_lat" in row
        res = await c.get("/api/v1/users", params={"city": "Pune", "page_size": 100}, headers=admin_headers)
        assert [u["full_name"] for u in res.json()["data"] if u["full_name"].startswith("Loc ")] == ["Loc Bbb"]
        res = await c.get("/api/v1/users", params={"pincode": "560101", "sort_by": "full_name", "sort_order": 1, "page_size": 100}, headers=admin_headers)
        got = [u["full_name"] for u in res.json()["data"] if u["full_name"].startswith("Loc ")]
        assert got == sorted(got) and len(got) == 2
        facets = (await c.get("/api/v1/users/location-facets", headers=admin_headers)).json()["data"]
        assert {"Bengaluru", "Pune"} <= set(facets["cities"])
    # The facet queries are served by the (role, home_*) indexes, not a collection scan.
    await db.users.create_index([("role", 1), ("home_center_id", 1)])
    await db.users.create_index([("role", 1), ("home_city", 1)])
    for q, idx in (({"role": "customer", "home_center_id": blr}, "role_1_home_center_id_1"), ({"role": "customer", "home_city": "Pune"}, "role_1_home_city_1")):
        plan = await db.command({"explain": {"find": "users", "filter": q}, "verbosity": "executionStats"})
        stats = plan["executionStats"]
        assert idx in str(plan["queryPlanner"]["winningPlan"])
        assert stats["totalDocsExamined"] <= stats["nReturned"]


@pytest.mark.asyncio
async def test_manager_scope_unchanged_and_coordinates_hidden(db, cleanup, world, admin_headers):
    blr, pune = world
    mine = await _customer(db, cleanup)
    other = await _customer(db, cleanup)
    for cid, pin in ((mine, "560101"), (other, "411101")):
        await _addr(db, cid, pincode=pin, lat=12.97 if pin == "560101" else 18.52, lng=77.59 if pin == "560101" else 73.85)
        await refresh_customer_location(db, cid)
    await _booking(db, mine, blr)
    manager = await make_manager(db, blr)
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    mh = _auth(manager, "manager", blr)
    async with _client() as c:
        # The unused customer-list route is gone (users list is admin-only).
        assert (await c.get("/api/v1/crm/customers", params={"center_id": blr}, headers=mh)).status_code in (404, 405, 422)
        # Opening a customer with no history with this center: still 404.
        assert (await c.get(f"/api/v1/crm/customers/{other}", headers=mh)).status_code == 404
        r360 = (await c.get(f"/api/v1/crm/customers/{mine}", headers=mh)).json()["data"]["profile"]
        assert r360["home_city"] == "Bengaluru" and "home_lat" not in r360
        # Admin sees coordinates.
        r360 = (await c.get(f"/api/v1/crm/customers/{mine}", headers=admin_headers)).json()["data"]["profile"]
        assert r360["home_lat"] == 12.97
        # The customer's own profile and a manager's list of users never carry them.
        me = await c.get("/api/v1/auth/me", headers=_auth(mine, "customer"))
        assert me.status_code == 200 and "home_center_id" not in me.text and "home_lat" not in me.text
        # Managers can't use the admin endpoints.
        assert (await c.get("/api/v1/users", headers=mh)).status_code in (401, 403)
        # The unused admin home-center override route is gone for everyone.
        assert (await c.put(f"/api/v1/users/{mine}/home-center", json={"center_id": pune}, headers=admin_headers)).status_code in (404, 405)
