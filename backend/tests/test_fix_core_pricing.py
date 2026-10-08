"""Core fix round 1 — pricing (audit 2026-10-07: PRICE-04, PRICE-02, PAY-15)."""
import itertools

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingCreateRequest, GroupVehicleRequest, ManagerBookingCreateRequest, QuickBookingLine
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id

pytestmark = pytest.mark.asyncio
_n = itertools.count(1)


async def _own_service(db, price: float) -> str:
    """A private copy of the seeded wash — tests may re-price it freely."""
    star = await db.services.find_one({"slug": "star-wash"})
    doc = {k: v for k, v in star.items() if k != "_id"}
    n = next(_n)
    doc.update({
        "slug": f"fc-price-wash-{n}-{ObjectId()}", "name": f"Price Test Wash {n}", "price": price,
        "vehicle_type_prices": {}, "discounted_price": None, "vehicle_type_discounted_prices": {}, "original_price": None,
        "prepaid_only": False, "charges_travel": False,
    })
    return str((await db.services.insert_one(doc)).inserted_id)


async def _quick_body(db, cu: dict, when: str, slot: str, svc: str, **extra) -> dict:
    raw = await db.users.find_one({"_id": ObjectId(cu["id"])})
    b = {"customer_name": "Price Person", "customer_phone": raw["phone"], "address_id": cu["address_id"],
         "lines": [{"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [svc]}],
         "scheduled_date": when, "scheduled_slot": slot, "payment_method": "cash"}
    b.update(extra)
    return b


# ---------------------------------------------------------------- PRICE-04


async def test_price04_quick_booking_refuses_a_price_rise_since_the_quote(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    svc = await _own_service(db, 400)
    async with h.client() as c:
        body = await _quick_body(db, cu, when, keys[0], svc)
        q = await c.post("/api/v1/bookings/quote", json={"lines": body["lines"], "address_id": cu["address_id"]}, headers=cu["h"])
        assert q.status_code == 200, q.text
        quoted = q.json()["data"]["total_amount"]
        assert quoted == 400
        await db.services.update_one({"_id": ObjectId(svc)}, {"$set": {"price": 550}})
        r = await c.post("/api/v1/bookings/quick", json={**body, "expected_total": quoted}, headers=cu["h"])
        assert r.status_code == 409, r.text
        err = r.json()
        assert err["error_code"] == "PRICE_CHANGED"
        assert err["details"]["total_amount"] == 550 and err["details"]["expected_total"] == 400
        assert await db.bookings.count_documents({"customer_id": cu["id"]}) == 0
        assert (await h.seat(db, cid, when, keys[0])).get("booked_count", 0) == 0
        r = await c.post("/api/v1/bookings/quick", json={**body, "expected_total": 550}, headers=cu["h"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["total_amount"] == 550


async def test_price04_a_price_drop_is_not_refused(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    svc = await _own_service(db, 300)
    async with h.client() as c:
        r = await c.post("/api/v1/bookings/quick", json={**await _quick_body(db, cu, when, keys[0], svc), "expected_total": 450}, headers=cu["h"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total_amount"] == 300


async def test_price04_single_and_group_endpoints_check_too(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    svc = await _own_service(db, 500)
    hatch = await get_hatchback_type_id(db)
    async with h.client() as c:
        r = await h.book(c, db, cu, when, keys[0], service_ids=[svc], expected_total=400)
        assert r.status_code == 409 and r.json()["error_code"] == "PRICE_CHANGED", r.text
        g = {"vehicles": [{"vehicle_type": hatch, "quantity": 2, "service_ids": [svc]}], "address_id": cu["address_id"],
             "scheduled_date": when, "scheduled_slot": keys[0], "payment_method": "cash", "expected_total": 800}
        r = await c.post("/api/v1/bookings/group", json=g, headers=cu["h"])
        assert r.status_code == 409 and r.json()["details"]["total_amount"] == 1000, r.text
        assert await db.bookings.count_documents({"customer_id": cu["id"]}) == 0
        r = await c.post("/api/v1/bookings/group", json={**g, "expected_total": 1000}, headers=cu["h"])
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------- PRICE-02


async def test_price02_duplicate_service_ids_collapse_to_one():
    s = str(ObjectId())
    other = str(ObjectId())
    assert BookingCreateRequest(vehicle_type="t", address_id="a", service_ids=[s, s, other, s], scheduled_date="2026-10-10", scheduled_slot="09:00-12:00").service_ids == [s, other]
    assert QuickBookingLine(vehicle_type="t", service_ids=[s, s, s]).service_ids == [s]
    assert GroupVehicleRequest(vehicle_type="t", service_ids=[s, s]).service_ids == [s]
    assert ManagerBookingCreateRequest(customer_id="c", vehicle_type="t", address_id="a", service_ids=[s, s], scheduled_date="2026-10-10", scheduled_slot="09:00-12:00").service_ids == [s]


async def test_price02_triple_star_is_priced_as_one_wash(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    cu = await h.customer(db, pin)
    svc = await _own_service(db, 350)
    async with h.client() as c:
        body = await _quick_body(db, cu, when, keys[0], svc)
        body["lines"][0]["service_ids"] = [svc, svc, svc]
        r = await c.post("/api/v1/bookings/quick", json=body, headers=cu["h"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total_amount"] == 350
    doc = await db.bookings.find_one({"customer_id": cu["id"]})
    assert doc["service_ids"] == [svc]


# ---------------------------------------------------------------- PAY-15


async def test_pay15_anonymous_quote_does_not_reveal_whether_a_phone_booked(db):
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    booked = await h.customer(db, pin)
    async with h.client() as c:
        assert (await h.book(c, db, booked, when, keys[0])).status_code == 200
        phone = (await db.users.find_one({"_id": ObjectId(booked["id"])}))["phone"]
        lines = [{"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [await get_star_wash_service_id(db)]}]
        known = (await c.post("/api/v1/bookings/quote", json={"lines": lines, "customer_phone": phone})).json()["data"]
        fresh = (await c.post("/api/v1/bookings/quote", json={"lines": lines, "customer_phone": "9000012345"})).json()["data"]
        nophone = (await c.post("/api/v1/bookings/quote", json={"lines": lines})).json()["data"]
        mine = (await c.post("/api/v1/bookings/quote", json={"lines": lines}, headers=booked["h"])).json()["data"]
    assert known == fresh == nophone
    assert known["first_time_eligible"] is False and known["first_time_pending"] is True
    assert known["total_amount"] == known["regular_subtotal"]
    # The signed-in customer is quoted as themselves (already booked once).
    assert mine["first_time_eligible"] is False and mine["first_time_pending"] is False
