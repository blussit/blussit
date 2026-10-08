"""Core fix round 1 — booking-side hooks for the PLANS owner (audit
2026-10-07: PASS-3 a pass covers only washes inside its period, SOC-4 a
car-bound pass is never auto-applied to a type-only booking)."""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.schemas.subscription_schema import SubscribeRequest
from app.services.subscription_service import UserSubscriptionService
from app.utils.timezone import IST, now_ist
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_subscription_plan

pytestmark = pytest.mark.asyncio


async def _pass(db, cu: dict, *, ends_on_offset: int, car_bound: bool = False) -> str:
    star = await get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=4)
    sub = await UserSubscriptionService(db).subscribe(cu["id"], SubscribeRequest(
        plan_id=plan_id, service_id=star,
        **({"vehicle_id": cu["vehicle_id"]} if car_bound else {"vehicle_type": await get_hatchback_type_id(db)}),
    ))
    day = now_ist().date() + timedelta(days=ends_on_offset)
    end = datetime(day.year, day.month, day.day, tzinfo=IST)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub["id"])}, {"$set": {"end_date": end}})
    return sub["id"]


async def _lines(db) -> list[dict]:
    return [{"vehicle_type": await get_hatchback_type_id(db), "quantity": 1, "service_ids": [await get_star_wash_service_id(db)]}]


async def _quick(c, db, cu: dict, when: str, slot: str):
    phone = (await db.users.find_one({"_id": ObjectId(cu["id"])}))["phone"]
    return await c.post("/api/v1/bookings/quick", json={
        "customer_name": "Pass Person", "customer_phone": phone, "address_id": cu["address_id"], "lines": await _lines(db),
        "scheduled_date": when, "scheduled_slot": slot, "payment_method": "cash",
    }, headers=cu["h"])


async def test_pass3_quote_equals_create_on_both_sides_of_the_pass_end(db):
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    sub_id = await _pass(db, cu, ends_on_offset=3)
    inside, keys = await h.slot_keys(db, cid, 2)
    outside, _ = await h.slot_keys(db, cid, 3)
    async with h.client() as c:
        for when, covered in ((inside, True), (outside, False)):
            q = await c.post("/api/v1/bookings/quote", json={"lines": await _lines(db), "address_id": cu["address_id"], "scheduled_date": when}, headers=cu["h"])
            assert q.status_code == 200, q.text
            quote = q.json()["data"]
            r = await _quick(c, db, cu, when, keys[0])
            assert r.status_code == 200, r.text
            created = r.json()["data"]
            assert created["total_amount"] == quote["total_amount"], (when, quote, created)
            assert (quote["lines"][0]["plan_covered"] == 1) is covered
            doc = await db.bookings.find_one({"_id": ObjectId(created["bookings"][0]["id"])})
            assert (doc.get("subscription_id") == sub_id) is covered


async def test_soc4_type_only_booking_never_takes_a_car_bound_pass(db):
    """SOC-4 is about SOCIETY passes (and custom passes): explicit-only, never
    auto-applied to a type-only booking. (Follow-up 2026-10-07: a car-bound
    STANDARD monthly pass is auto-applied again and books its own car — see
    the companion test below and tests/test_feat_followup_passes.py.)"""
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    sub_id = await _pass(db, cu, ends_on_offset=20, car_bound=True)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"society_id": str(ObjectId()), "plan_kind": "society"}})
    when, keys = await h.slot_keys(db, cid, 2)
    async with h.client() as c:
        r = await _quick(c, db, cu, when, keys[0])
    assert r.status_code == 200, r.text
    doc = await db.bookings.find_one({"_id": ObjectId(r.json()["data"]["bookings"][0]["id"])})
    assert doc.get("subscription_id") is None and doc["total_amount"] > 0


async def test_type_only_booking_takes_a_car_bound_standard_pass_and_books_that_car(db):
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    sub_id = await _pass(db, cu, ends_on_offset=20, car_bound=True)
    when, keys = await h.slot_keys(db, cid, 2)
    async with h.client() as c:
        r = await _quick(c, db, cu, when, keys[0])
    assert r.status_code == 200, r.text
    doc = await db.bookings.find_one({"_id": ObjectId(r.json()["data"]["bookings"][0]["id"])})
    assert doc["subscription_id"] == sub_id and doc["total_amount"] == 0 and doc["vehicle_id"] == cu["vehicle_id"]


async def test_pass3_staff_reschedule_past_the_pass_period_is_refused(db):
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    mgr = await h.manager(db, cid)
    sub_id = await _pass(db, cu, ends_on_offset=3)
    when, keys = await h.slot_keys(db, cid, 1)
    late, _ = await h.slot_keys(db, cid, 4)
    async with h.client() as c:
        r = await _quick(c, db, cu, when, keys[-1])
        assert r.status_code == 200, r.text
        bid = r.json()["data"]["bookings"][0]["id"]
        assert (await db.bookings.find_one({"_id": ObjectId(bid)}))["subscription_id"] == sub_id
        r = await c.post(f"/api/v1/bookings/{bid}/reschedule", json={"scheduled_date": late, "scheduled_slot": keys[0]}, headers=mgr["h"])
        assert r.status_code == 400, r.text
        assert "pass covers washes" in r.json()["message"]
    doc = await db.bookings.find_one({"_id": ObjectId(bid)})
    assert doc["scheduled_date"] == datetime.strptime(when, "%Y-%m-%d")
