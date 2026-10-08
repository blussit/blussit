"""Coordinator follow-ups (2026-10-07) on booking edits:

(4) A BIKE booking's edit / on-site add may carry the add-a-bike line
    (Extra Bike Wash × N) exactly as the booking page books 3+ bikes
    (smallest variant + Extra Bike Wash × the rest) — a Bike booking edited
    from 2 to 4 bikes is priced like a fresh 4-bike booking. On a CAR it is
    still refused (QA 10-07).
(5) The edit's dry-run / save answer carries `travel_charge` (the visit's
    distance charge after the edit) and per car.
Local Mongo only."""
import itertools

import pytest

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest, BookingEditRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


async def _svc(db, name: str) -> str:
    return str((await db.services.find_one({"name": name, "is_deleted": {"$ne": True}}))["_id"])


async def _bike_type(db) -> str:
    return str((await db.vehicle_types.find_one({"slug": "bike"}))["_id"])


async def _bike_booking(db, s: dict, slot: int, service_ids: list[str], quantities: dict | None = None) -> dict:
    return await BookingService(db).create_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=await _bike_type(db), address_id=s["cu"]["address_id"], service_ids=service_ids,
        service_quantities=quantities or {}, scheduled_date=s["when"], scheduled_slot=s["keys"][slot], payment_method="cash",
    ), _allow_pinless=True)


async def test_bike_booking_edited_from_two_to_four_bikes_prices_like_a_fresh_one(db):
    s = await fb.rig(db)
    one, two, extra = await _svc(db, "Bike Wash"), await _svc(db, "Bike Wash (2 bikes)"), await _svc(db, "Extra Bike Wash")
    made = await _bike_booking(db, s, 0, [two])
    out = await BookingService(db).edit_booking(
        made["id"], BookingEditRequest(service_ids=[one, extra], service_quantities={extra: 3}), s["cu"]["id"], "customer",
    )
    edited = await fb.doc(db, made["id"])
    assert set(edited["service_ids"]) == {one, extra} and edited["service_quantities"] == {extra: 3}
    fresh = await fb.doc(db, (await _bike_booking(db, s, 1, [one, extra], {extra: 3}))["id"])
    assert edited["subtotal"] == fresh["subtotal"] and edited["total_amount"] == fresh["total_amount"]
    assert out["total_amount"] == fresh["total_amount"]


async def test_on_site_add_of_extra_bikes_on_a_bike_booking(db):
    s = await fb.rig(db)
    two, extra = await _svc(db, "Bike Wash (2 bikes)"), await _svc(db, "Extra Bike Wash")
    made = await _bike_booking(db, s, 0, [two])
    before = (await fb.doc(db, made["id"]))["total_amount"]
    out = await BookingService(db).add_services_on_site(
        made["id"], [extra], {extra: 2}, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"],
    )
    assert [a["name"] for a in out["added"]] == ["Extra Bike Wash"]
    after = await fb.doc(db, made["id"])
    price = float((await db.services.find_one({"name": "Extra Bike Wash"}))["price"])
    assert after["total_amount"] == pytest.approx(before + 2 * price)


async def test_extra_bike_wash_still_refused_on_a_car(db):
    s = await fb.rig(db)
    star, extra = await fb.get_star_wash_service_id(db), await _svc(db, "Extra Bike Wash")
    car = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    svc = BookingService(db)
    with pytest.raises(BadRequestException, match="isn't offered"):
        await svc.edit_booking(car["id"], BookingEditRequest(service_ids=[star, extra], service_quantities={extra: 2}), s["cu"]["id"], "customer")
    with pytest.raises(BadRequestException, match="isn't offered"):
        await svc.add_services_on_site(car["id"], [extra], {}, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])


async def test_edit_answer_carries_the_travel_charge(db):
    s = await fb.rig(db)
    star = await db.services.find_one({"slug": "star-wash"})
    travel_svc = {k: v for k, v in star.items() if k != "_id"}
    n = next(_seq)
    travel_svc.update({"name": f"M2 Travel Wash {n}", "slug": f"m2-travel-wash-{n}", "charges_travel": True, "prepaid_only": False})
    travel_id = str((await db.services.insert_one(travel_svc)).inserted_id)
    made = await BookingService(db).create_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[travel_id],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ), _allow_pinless=True)
    body = {"address": {"line1": "Far Lane 9, Indore", "city": "Indore", "state": "MP", "pincode": s["pin"],
                        "latitude": 22.98, "longitude": 75.8}}
    async with h.client() as c:
        r = await c.patch(f"/api/v1/bookings/{made['id']}", json={**body, "dry_run": True}, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        preview = r.json()["data"]
        r = await c.patch(f"/api/v1/bookings/{made['id']}", json=body, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        saved = r.json()["data"]
    d = await fb.doc(db, made["id"])
    assert d["travel_charge"] > 0
    assert preview["travel_charge"] == saved["travel_charge"] == d["travel_charge"]
    assert preview["cars"][0]["travel_charge"] == saved["cars"][0]["travel_charge"] == d["travel_charge"]
