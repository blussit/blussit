"""
Theater-seat slot holds (AUDIT.md M1). The contracts:
  - a hold shrinks availability for everyone else, immediately;
  - the last seat can be held by exactly ONE of N concurrent customers;
  - the holder converts their hold into a booking even when the slot
    shows full to everyone else; a non-holder is refused;
  - releasing / expiry frees the seat (sweeper decrements exactly once);
  - re-holding your own slot renews instead of double-counting.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest
from app.services.booking_service import BookingService
from app.utils.timezone import now_ist

from tests.factories import get_star_wash_service_id, get_hatchback_type_id, make_customer_with_vehicle, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    foam = await get_star_wash_service_id(db)
    center_id = await make_service_center(
        db, working_hours_start="09:00", working_hours_end="21:00", slot_duration_minutes=180, default_slot_capacity=1
    )
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("slot_holds", {"service_center_id": center_id}))
    date_str = (now_ist().date() + timedelta(days=2)).isoformat()
    svc = BookingService(db)
    slots = await svc.available_slots(center_id, date_str)
    return {"db": db, "svc": svc, "center_id": center_id, "date": date_str, "slot": slots[0]["key"],
            "hatchback": hatchback, "foam": foam}


@pytest.mark.asyncio
async def test_hold_shrinks_availability_and_release_restores_it(rig):
    svc, cid, date, slot = rig["svc"], rig["center_id"], rig["date"], rig["slot"]
    out = await svc.hold_slot("holderA00", cid, date, slot)
    assert out["held"] and not out["renewed"]

    slots = await svc.available_slots(cid, date)
    assert next(x for x in slots if x["key"] == slot)["status"] == "full"  # capacity 1, all held

    # Renewal doesn't double-count.
    again = await svc.hold_slot("holderA00", cid, date, slot)
    assert again["renewed"] is True
    doc = await rig["db"].slot_capacity.find_one({"service_center_id": cid, "date": date, "slot_key": slot})
    assert doc["held_count"] == 1

    await svc.release_hold("holderA00", cid, date, slot)
    slots = await svc.available_slots(cid, date)
    assert next(x for x in slots if x["key"] == slot)["status"] != "full"
    doc = await rig["db"].slot_capacity.find_one({"service_center_id": cid, "date": date, "slot_key": slot})
    assert doc["held_count"] == 0


@pytest.mark.asyncio
async def test_last_seat_goes_to_exactly_one_of_many_concurrent_holders(rig):
    svc, cid, date, slot = rig["svc"], rig["center_id"], rig["date"], rig["slot"]
    results = await asyncio.gather(
        *[svc.hold_slot(f"racer{i:03d}", cid, date, slot) for i in range(6)],
        return_exceptions=True,
    )
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, BadRequestException)]
    assert len(winners) == 1 and len(losers) == 5
    doc = await rig["db"].slot_capacity.find_one({"service_center_id": cid, "date": date, "slot_key": slot})
    assert doc["held_count"] == 1


@pytest.mark.asyncio
async def test_holder_books_a_full_looking_slot_and_stranger_cannot(rig, db, cleanup):
    svc, cid, date, slot = rig["svc"], rig["center_id"], rig["date"], rig["slot"]
    holder_cust, holder_veh, holder_addr = await make_customer_with_vehicle(db, rig["hatchback"])
    stranger_cust, stranger_veh, stranger_addr = await make_customer_with_vehicle(db, rig["hatchback"])
    for uid in (holder_cust, stranger_cust):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
        cleanup.append(("vehicles", {"owner_id": uid}))
        cleanup.append(("addresses", {"owner_id": uid}))
        cleanup.append(("bookings", {"customer_id": uid}))
        cleanup.append(("notifications", {"user_id": uid}))
        cleanup.append(("purchase_confirmations", {"customer_id": uid}))

    await svc.hold_slot(holder_cust, cid, date, slot)

    def payload(vehicle_id, address_id):
        return BookingCreateRequest(
            vehicle_id=vehicle_id, address_id=address_id, service_ids=[rig["foam"]],
            scheduled_date=datetime.strptime(date, "%Y-%m-%d"), scheduled_slot=slot,
        )

    # The stranger sees a full slot and is refused at create.
    with pytest.raises(BadRequestException, match="fully booked"):
        await svc.create_booking(stranger_cust, payload(stranger_veh, stranger_addr))

    # The holder converts the hold — their own hold never blocks them.
    booking = await svc.create_booking(holder_cust, payload(holder_veh, holder_addr))
    assert booking["scheduled_slot"] == slot
    doc = await db.slot_capacity.find_one({"service_center_id": cid, "date": date, "slot_key": slot})
    assert doc["booked_count"] == 1 and doc["held_count"] == 0
    assert await db.slot_holds.count_documents({"service_center_id": cid}) == 0


@pytest.mark.asyncio
async def test_expired_hold_is_swept_and_seat_returns(rig, db):
    svc, cid, date, slot = rig["svc"], rig["center_id"], rig["date"], rig["slot"]
    await svc.hold_slot("expireme0", cid, date, slot)
    await db.slot_holds.update_one(
        {"service_center_id": cid, "holder_id": "expireme0"},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    # available_slots sweeps opportunistically — the seat is back.
    slots = await svc.available_slots(cid, date)
    assert next(x for x in slots if x["key"] == slot)["status"] != "full"
    doc = await db.slot_capacity.find_one({"service_center_id": cid, "date": date, "slot_key": slot})
    assert doc["held_count"] == 0
    assert await db.slot_holds.count_documents({"service_center_id": cid}) == 0
