"""Core fix round 1 — one date representation (audit 2026-10-07: BOOK-01,
BOOK-05). scheduled_date is the IST business calendar day, stored as naive
IST midnight, whatever form the client sent."""
import asyncio
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.schemas.booking_schema import (
    BookingCreateRequest,
    BookingGroupCreateRequest,
    BookingRescheduleRequest,
    ManagerBookingCreateRequest,
)
from tests import test_fix_core_helpers as h
from tests.factories import get_suv_type_id

pytestmark = pytest.mark.asyncio


def _forms(when: str) -> list[str]:
    d = datetime.strptime(when, "%Y-%m-%d")
    utc_prev = (d - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [when, f"{when}T00:00:00+05:30", utc_prev]


@pytest.mark.parametrize("model", ["create", "manager", "reschedule"])
async def test_book01_every_date_form_normalizes_to_ist_midnight(model):
    when = "2026-10-10"
    target = datetime(2026, 10, 10)
    for raw in [*_forms(when), f"{when}T23:59:00+05:30", f"{when}T10:30:00"]:
        if model == "create":
            m = BookingCreateRequest(vehicle_type="x", address_id="a", scheduled_date=raw, scheduled_slot="09:00-12:00")
        elif model == "manager":
            m = ManagerBookingCreateRequest(customer_id="c", vehicle_type="x", address_id="a", scheduled_date=raw, scheduled_slot="09:00-12:00")
        else:
            m = BookingRescheduleRequest(scheduled_date=raw, scheduled_slot="09:00-12:00")
        assert m.scheduled_date == target and m.scheduled_date.tzinfo is None, (raw, m.scheduled_date)


async def test_book01_group_request_date_is_a_plain_day():
    for raw in _forms("2026-10-10"):
        g = BookingGroupCreateRequest(vehicles=[{"vehicle_type": "x"}], address_id="a", scheduled_date=raw, scheduled_slot="09:00-12:00")
        assert g.scheduled_date == "2026-10-10", raw


async def test_book01_garbage_date_is_refused():
    with pytest.raises(ValueError):
        BookingRescheduleRequest(scheduled_date="10/10/2026", scheduled_slot="09:00-12:00")


async def test_book01_tz_aware_booking_lands_and_releases_on_its_own_day(db):
    cid, pin = await h.center(db)
    d_prev, keys = await h.slot_keys(db, cid, 2)
    d_target, _ = await h.slot_keys(db, cid, 3)
    slot = keys[0]
    await h.set_cap(db, cid, d_prev, slot, 5)
    await h.set_cap(db, cid, d_target, slot, 5)
    neighbours = [await h.customer(db, pin) for _ in range(2)]
    cu = await h.customer(db, pin)
    mgr = await h.manager(db, cid)
    async with h.client() as c:
        for n in neighbours:
            assert (await h.book(c, db, n, d_prev, slot)).status_code == 200
        for raw in _forms(d_target):
            r = await h.book(c, db, cu, d_target, slot, scheduled_date=raw)
            assert r.status_code == 200, (raw, r.text)
            bid = r.json()["data"]["id"]
            doc = await db.bookings.find_one({"_id": ObjectId(bid)})
            assert doc["scheduled_date"] == datetime.strptime(d_target, "%Y-%m-%d"), raw
            assert doc["seat_key"]["date"] == d_target
            assert (await h.state(db, cid, d_target, slot))["booked"] == 1
            q = await c.get(f"/api/v1/bookings/center/{cid}", params={"date_from": d_target, "date_to": d_target}, headers=mgr["h"])
            assert bid in [b["id"] for b in q.json()["data"]]
            r = await c.post(f"/api/v1/bookings/{bid}/cancel", json={"reason": "changed mind"}, headers=cu["h"])
            assert r.status_code == 200
            assert (await h.state(db, cid, d_target, slot))["booked"] == 0
            prev = await h.state(db, cid, d_prev, slot)
            assert prev["booked"] == prev["owners"] == 2, (raw, prev)


async def test_book01_reschedule_with_an_aware_date_moves_to_that_day(db):
    cid, pin = await h.center(db)
    d1, keys = await h.slot_keys(db, cid, 2)
    d2, _ = await h.slot_keys(db, cid, 3)
    cu = await h.customer(db, pin)
    async with h.client() as c:
        bid = (await h.book(c, db, cu, d1, keys[0])).json()["data"]["id"]
        r = await c.post(f"/api/v1/bookings/{bid}/reschedule", json={"scheduled_date": f"{d2}T00:00:00+05:30", "scheduled_slot": keys[0]}, headers=cu["h"])
        assert r.status_code == 200, r.text
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    assert doc["scheduled_date"] == datetime.strptime(d2, "%Y-%m-%d")
    assert doc["seat_key"]["date"] == d2
    assert (await h.state(db, cid, d1, keys[0]))["booked"] == 0
    assert (await h.state(db, cid, d2, keys[0]))["booked"] == 1


# ---------------------------------------------------------------- BOOK-05


async def test_book05_two_tabs_two_car_types_one_slot_only_one_booking(db):
    suv = await get_suv_type_id(db)
    created = []
    for i in range(6):
        cid, pin = await h.center(db)
        when, keys = await h.slot_keys(db, cid, 2 + i % 4)
        slot = keys[i % len(keys)]
        cu = await h.customer(db, pin)
        async with h.client() as c:
            rs = await asyncio.gather(
                h.book(c, db, cu, when, slot),
                h.book(c, db, cu, when, slot, vehicle_type=suv),
                # the same day/slot sent as a datetime variant must not dodge it either
                h.book(c, db, cu, when, slot, scheduled_date=f"{when}T00:00:01"),
            )
        ok = [r for r in rs if r.status_code == 200]
        created.append(len(ok))
        assert len(ok) == 1, [r.text for r in rs]
        assert all(r.status_code == 400 for r in rs if r.status_code != 200)
        live = await db.bookings.count_documents({"customer_id": cu["id"], "status": {"$ne": "cancelled"}})
        assert live == 1
    print("\n[two tabs]", created)
