"""
"Book again" — repeating a past booking without re-picking everything.

The replay itself is the customer app's job (it reads the old booking and
prefills the wizard), but it can only replay what the booking actually
remembers. These tests pin the memory:

  - a booking sold as a COMBO remembers it was a combo, not just the loose
    services inside it — otherwise a repeat would re-price the bundle as
    separate items, and the booking would read back as a list of services
    the customer never picked;
  - per-unit add-on counts ("Extra Bike Wash ×2") survive, so a repeat
    brings back the same quantities;
  - a CANCELLED booking still carries everything a repeat needs, which is
    the case that matters most: a cancellation is exactly when a customer
    wants this booking back.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.services.booking_service import BookingService
from app.utils.timezone import now_ist

from tests.factories import get_hatchback_type_id, make_customer_with_vehicle, make_service_center

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def rig(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id})]:
        cleanup.append((coll, q))

    star = await db.services.find_one({"name": "Star Wash", "is_deleted": {"$ne": True}})
    extra_bike = await db.services.find_one({"name": "Extra Bike Wash", "is_deleted": {"$ne": True}})
    assert star and extra_bike, "seed() should have created these services"

    combo_id = str((await db.combo_offers.insert_one({
        "name": "Rebook Test Combo",
        "slug": f"rebook-test-combo-{ObjectId()}",
        "service_ids": [str(star["_id"])],
        "vehicle_types": [hatchback],
        "price": 777.0,
        "is_active": True,
        "is_featured": False,
        "display_order": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "is_deleted": False,
    })).inserted_id)
    cleanup.append(("combo_offers", {"_id": ObjectId(combo_id)}))

    when = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await BookingService(db).available_slots(center_id, when)
    slot = next(s["key"] for s in slots if s["status"] == "available")
    return {
        "customer_id": customer_id, "vehicle_id": vehicle_id, "address_id": address_id,
        "center_id": center_id, "star": star, "extra_bike": extra_bike,
        "combo_id": combo_id, "when": when, "slot": slot,
    }


def _request(rig, **overrides) -> BookingCreateRequest:
    base = dict(
        vehicle_id=rig["vehicle_id"],
        address_id=rig["address_id"],
        scheduled_date=rig["when"],
        scheduled_slot=rig["slot"],
        payment_method="cash",
    )
    base.update(overrides)
    return BookingCreateRequest(**base)


async def test_combo_booking_remembers_it_was_a_combo(rig, db):
    """Without combo_id on the booking there is no way back to the bundle:
    the services inside it price separately, so a repeat would quote a
    different number for the same wash."""
    bs = BookingService(db)
    booking = await bs.create_booking(
        rig["customer_id"], _request(rig, combo_id=rig["combo_id"]), _skip_verification_gate=True
    )

    stored = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert stored["combo_id"] == rig["combo_id"]
    # ...and the bundle's own price, not the sum of its parts.
    assert stored["subtotal"] == 777.0

    # It also reads back AS the combo — the name the customer was sold.
    read = await bs.get_booking_with_history(booking["id"], rig["customer_id"], "customer")
    assert read["combo_name"] == "Rebook Test Combo"
    assert read["combo_id"] == rig["combo_id"]


async def test_plain_booking_carries_no_combo_id(rig, db):
    """The other half of the same rule: a loose pick must NOT read back as
    a bundle, or a repeat would silently turn it into one."""
    bs = BookingService(db)
    booking = await bs.create_booking(
        rig["customer_id"], _request(rig, service_ids=[str(rig["star"]["_id"])]), _skip_verification_gate=True
    )
    read = await bs.get_booking_with_history(booking["id"], rig["customer_id"], "customer")
    assert read.get("combo_id") is None
    assert read["combo_name"] is None
    assert read["service_names"] == ["Star Wash"]


async def test_addon_quantities_survive_for_the_repeat(rig, db):
    """"Star Wash + 2 bikes" has to come back as 2 bikes, not 1."""
    bs = BookingService(db)
    extra_id = str(rig["extra_bike"]["_id"])
    booking = await bs.create_booking(
        rig["customer_id"],
        _request(rig, service_ids=[str(rig["star"]["_id"]), extra_id], service_quantities={extra_id: 2}),
        _skip_verification_gate=True,
    )
    read = await bs.get_booking_with_history(booking["id"], rig["customer_id"], "customer")
    assert read["service_quantities"][extra_id] == 2
    assert any("×2" in name for name in read["service_names"])


async def test_cancelled_booking_still_holds_everything_a_repeat_needs(rig, db):
    """The case the button exists for. Cancelling must not strip the car,
    the address or the services off the booking."""
    bs = BookingService(db)
    booking = await bs.create_booking(
        rig["customer_id"], _request(rig, service_ids=[str(rig["star"]["_id"])]), _skip_verification_gate=True
    )
    await bs.cancel_booking(
        booking["id"], BookingCancelRequest(reason="Changed my mind"), rig["customer_id"], "customer"
    )

    read = await bs.get_booking_with_history(booking["id"], rig["customer_id"], "customer")
    assert read["status"] == "cancelled"
    assert read["vehicle_id"] == rig["vehicle_id"]
    assert read["address_id"] == rig["address_id"]
    assert read["service_ids"] == [str(rig["star"]["_id"])]
