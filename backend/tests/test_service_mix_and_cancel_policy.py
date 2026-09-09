"""
Catalogue add-on/base rules + cancellation policy phase 1.

Service mix (enforced in create_booking for every channel):
  - an add-on can never be booked alone;
  - car add-ons don't attach to bike bookings and vice versa — EXCEPT the
    deliberate combo: a car booking that adds bikes (Extra Bike Wash ×N)
    may carry Bike Polish for those bikes;
  - bike polish quantity can never exceed the bikes in the booking;
  - one pick per variant group (no "2 bikes" + "4 bikes" together);
  - per-unit quantities multiply the price.

Cancellation policy phase 1 (charges are documented, NOT enforced):
  - customer self-cancel only while PENDING and >4h before the slot;
  - staff cancel is unaffected (customer calls in).
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.services.booking_service import BookingService

from tests.factories import get_hatchback_type_id, make_customer_with_vehicle, make_manager, make_service_center


async def _svc(db, name: str) -> dict:
    doc = await db.services.find_one({"name": name, "is_deleted": {"$ne": True}})
    assert doc, f"seed() should have created the '{name}' service"
    return doc


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    bike_type = await db.vehicle_types.find_one({"name": {"$regex": "bike", "$options": "i"}})
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("notifications", {}))
    cleanup.append(("purchase_confirmations", {"customer_id": customer_id}))
    return {
        "db": db, "center_id": center_id, "customer_id": customer_id, "vehicle_id": vehicle_id,
        "address_id": address_id, "car_vehicle": {"vehicle_type": hatchback},
        "bike_vehicle": {"vehicle_type": str(bike_type["_id"])},
    }


# ---------------------------------------------------------------- mix rules


@pytest.mark.asyncio
async def test_addon_alone_is_rejected(rig, db):
    bs = BookingService(db)
    polish = await _svc(db, "Exterior Polish")
    with pytest.raises(BadRequestException, match="along with a main service"):
        await bs._validate_service_mix([polish], rig["car_vehicle"], {})


@pytest.mark.asyncio
async def test_car_addon_rejected_on_bike_booking(rig, db):
    bs = BookingService(db)
    bike_wash = await _svc(db, "Bike Wash")
    ext_polish = await _svc(db, "Exterior Polish")
    with pytest.raises(BadRequestException, match="isn't available for this vehicle"):
        await bs._validate_service_mix([bike_wash, ext_polish], rig["bike_vehicle"], {})


@pytest.mark.asyncio
async def test_bike_polish_on_car_requires_added_bikes(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")
    bike_polish = await _svc(db, "Bike Polish")
    with pytest.raises(BadRequestException, match="add a bike"):
        await bs._validate_service_mix([star, bike_polish], rig["car_vehicle"], {})


@pytest.mark.asyncio
async def test_car_plus_bikes_combo_with_polish_ok(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")
    extra_bike = await _svc(db, "Extra Bike Wash")
    bike_polish = await _svc(db, "Bike Polish")
    q = await bs._validate_service_mix(
        [star, extra_bike, bike_polish], rig["car_vehicle"],
        {str(extra_bike["_id"]): 2, str(bike_polish["_id"]): 2},
    )
    assert q[str(extra_bike["_id"])] == 2 and q[str(bike_polish["_id"])] == 2


@pytest.mark.asyncio
async def test_bike_polish_capped_at_bike_count(rig, db):
    bs = BookingService(db)
    # Car booking with 1 added bike but 2 polishes -> rejected.
    star = await _svc(db, "Star Wash")
    extra_bike = await _svc(db, "Extra Bike Wash")
    bike_polish = await _svc(db, "Bike Polish")
    with pytest.raises(BadRequestException, match="per bike"):
        await bs._validate_service_mix(
            [star, extra_bike, bike_polish], rig["car_vehicle"],
            {str(extra_bike["_id"]): 1, str(bike_polish["_id"]): 2},
        )
    # Bike booking on the 2-bike variant with 3 polishes -> rejected; 2 -> fine.
    two_bikes = await _svc(db, "Bike Wash (2 bikes)")
    with pytest.raises(BadRequestException, match="per bike"):
        await bs._validate_service_mix([two_bikes, bike_polish], rig["bike_vehicle"], {str(bike_polish["_id"]): 3})
    q = await bs._validate_service_mix([two_bikes, bike_polish], rig["bike_vehicle"], {str(bike_polish["_id"]): 2})
    assert q[str(bike_polish["_id"])] == 2


@pytest.mark.asyncio
async def test_bike_booking_counts_bikes_as_base_plus_extras(rig, db):
    """The UI's single −/+ counter books base wash + N extra-bike lines
    (₹99 + ₹60 each additional) — the extra-bike add-on is valid on bike
    bookings too, and polish caps at base + extras."""
    bs = BookingService(db)
    bike_wash = await _svc(db, "Bike Wash")
    extra_bike = await _svc(db, "Extra Bike Wash")
    bike_polish = await _svc(db, "Bike Polish")
    ids = [bike_wash, extra_bike, bike_polish]
    # 1 base + 2 extras = 3 bikes; polish for all 3 is fine…
    q = await bs._validate_service_mix(ids, rig["bike_vehicle"], {str(extra_bike["_id"]): 2, str(bike_polish["_id"]): 3})
    assert q[str(extra_bike["_id"])] == 2 and q[str(bike_polish["_id"])] == 3
    # …but polish for a 4th bike that isn't there is not.
    with pytest.raises(BadRequestException, match="per bike"):
        await bs._validate_service_mix(ids, rig["bike_vehicle"], {str(extra_bike["_id"]): 2, str(bike_polish["_id"]): 4})


@pytest.mark.asyncio
async def test_two_variants_of_same_group_rejected(rig, db):
    bs = BookingService(db)
    one = await _svc(db, "Bike Wash")
    two = await _svc(db, "Bike Wash (2 bikes)")
    with pytest.raises(BadRequestException, match="one option"):
        await bs._validate_service_mix([one, two], rig["bike_vehicle"], {})


@pytest.mark.asyncio
async def test_quantity_only_for_per_unit_addons(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")
    with pytest.raises(BadRequestException, match="quantity"):
        await bs._validate_service_mix([star], rig["car_vehicle"], {str(star["_id"]): 2})


@pytest.mark.asyncio
async def test_quantities_multiply_price_end_to_end(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")
    extra_bike = await _svc(db, "Extra Bike Wash")
    bike_polish = await _svc(db, "Bike Polish")
    payload = BookingCreateRequest(
        vehicle_id=rig["vehicle_id"], address_id=rig["address_id"],
        service_ids=[str(star["_id"]), str(extra_bike["_id"]), str(bike_polish["_id"])],
        service_quantities={str(extra_bike["_id"]): 3, str(bike_polish["_id"]): 2},
        scheduled_date=datetime.now(timezone.utc) + timedelta(days=2),
        scheduled_slot="09:00-12:00",
    )
    booking = await bs.create_booking(rig["customer_id"], payload)
    hatch = rig["car_vehicle"]["vehicle_type"]
    expected = (
        bs._resolve_price(star, hatch, True)
        + 3 * bs._resolve_price(extra_bike, hatch, True)
        + 2 * bs._resolve_price(bike_polish, hatch, True)
    )
    assert booking["subtotal"] == round(expected, 2)
    assert booking["service_quantities"] == {str(extra_bike["_id"]): 3, str(bike_polish["_id"]): 2}


# ------------------------------------------------- cancellation policy p1


def _payload(rig, star_id, days=2):
    return BookingCreateRequest(
        vehicle_id=rig["vehicle_id"], address_id=rig["address_id"], service_ids=[star_id],
        scheduled_date=datetime.now(timezone.utc) + timedelta(days=days), scheduled_slot="09:00-12:00",
    )


@pytest.mark.asyncio
async def test_customer_can_cancel_early_but_not_inside_4h_window(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")

    # >4h before the slot: fine.
    early = await bs.create_booking(rig["customer_id"], _payload(rig, str(star["_id"]), days=2))
    out = await bs.cancel_booking(early["id"], BookingCancelRequest(reason="test"), rig["customer_id"], "customer")
    assert out["status"] == "cancelled"

    # Same booking shape but with the slot now <4h away: locked for the
    # customer, still open for the manager (customer calls in).
    late = await bs.create_booking(rig["customer_id"], _payload(rig, str(star["_id"]), days=3))
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    await db.bookings.update_one(
        {"_id": ObjectId(late["id"])},
        {"$set": {"slot_start": soon, "slot_end": soon + timedelta(hours=3)}},
    )
    with pytest.raises(BadRequestException, match="Cancellations close"):
        await bs.cancel_booking(late["id"], BookingCancelRequest(reason="test"), rig["customer_id"], "customer")
    manager_id = await make_manager(db, rig["center_id"])
    result = await bs.cancel_booking(late["id"], BookingCancelRequest(reason="customer called"), manager_id, "manager", rig["center_id"])
    assert result["status"] == "cancelled"
    await db.users.delete_one({"_id": ObjectId(manager_id)})


@pytest.mark.asyncio
async def test_customer_cannot_cancel_once_assigned(rig, db):
    bs = BookingService(db)
    star = await _svc(db, "Star Wash")
    booking = await bs.create_booking(rig["customer_id"], _payload(rig, str(star["_id"]), days=4))
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"status": "assigned"}})
    with pytest.raises(BadRequestException, match="captain is already"):
        await bs.cancel_booking(booking["id"], BookingCancelRequest(reason="test"), rig["customer_id"], "customer")
