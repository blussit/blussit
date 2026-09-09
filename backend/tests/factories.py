"""
Reusable test-data builders — the "build once, reuse per feature" layer the
spec explicitly asked for (Section 30: "add test cases according to
features so that we can test them no need to create again and again").
Every builder inserts directly via the raw `db` handle (not through a
service/schema layer) since that's what a *fixture* should do: set up
preconditions fast and explicitly, independent of the business logic under
test. Each returns plain string ids, matching what the service layer
expects everywhere in this codebase.

All document shapes here are lifted directly from live-tested scripts run
against the real dev database earlier in this project (not guessed), so
they're known-good against the actual Pydantic models.
"""
import itertools
from datetime import datetime, timezone

from app.core.security import hash_password

_counter = itertools.count(1)


def _n() -> int:
    """Monotonic counter for building unique emails/phones/plates within a
    single test session — avoids relying on Date.now()/random (unavailable
    in some contexts here) or colliding across tests that don't clean up
    in time."""
    return next(_counter)


async def make_service_center(
    db,
    *,
    name: str | None = None,
    working_hours_start: str = "09:00",
    working_hours_end: str = "19:00",
    slot_duration_minutes: int = 180,
    default_slot_capacity: int = 20,
    max_bookings_per_day: int | None = None,
    pincode: str = "452099",
) -> str:
    n = _n()
    doc = {
        "name": name or f"Test Center {n}",
        "code": f"TESTCTR{n:05d}",
        "location": {
            "address": "Test Street",
            "city": "Indore",
            "state": "MP",
            "pincode": pincode,
            "latitude": 22.7,
            "longitude": 75.8,
            "service_pincodes": [pincode],
            "radius_km": 6.0,
        },
        "working_hours_start": working_hours_start,
        "working_hours_end": working_hours_end,
        "slot_duration_minutes": slot_duration_minutes,
        "default_slot_capacity": default_slot_capacity,
        "is_active": True,
        "is_deleted": False,
    }
    if max_bookings_per_day is not None:
        doc["max_bookings_per_day"] = max_bookings_per_day
    result = await db.service_centers.insert_one(doc)
    return str(result.inserted_id)


async def make_customer(db, *, name: str | None = None, phone_verified: bool = True) -> str:
    # phone_verified defaults True — the vast majority of existing tests
    # create a customer and immediately call create_booking/subscribe,
    # which is now gated on this flag (see PhoneNotVerifiedException).
    # Pass phone_verified=False explicitly for tests that are specifically
    # exercising that gate (see tests/test_phone_verification.py).
    n = _n()
    now = datetime.now(timezone.utc)
    result = await db.users.insert_one({
        "full_name": name or f"Test Customer {n}",
        "email": f"test.customer.{n}@example.com",
        "phone": f"9{n:09d}",
        "password_hash": hash_password("Test@12345"),
        "role": "customer",
        "status": "active",
        "phone_verified": phone_verified,
        "phone_verified_at": now if phone_verified else None,
        "is_deleted": False,
        # UserPublic.from_doc (used by AuthService.login/_issue_tokens)
        # requires created_at — real accounts always have it via
        # BaseRepository.create()'s auto-set; this raw insert needs it too
        # for any test that logs in as a factory-created customer.
        "created_at": now,
        "updated_at": now,
    })
    return str(result.inserted_id)


async def make_vehicle(db, owner_id: str, vehicle_type_id: str, *, registration_number: str | None = None, is_default: bool = True) -> str:
    n = _n()
    result = await db.vehicles.insert_one({
        "owner_id": owner_id,
        "vehicle_type": vehicle_type_id,
        "brand": "Maruti",
        "model": "Swift",
        "registration_number": registration_number or f"MP09TS{n:04d}",
        "registration_number_normalized": (registration_number or f"MP09TS{n:04d}").upper().replace(" ", "").replace("-", ""),
        "is_default": is_default,
        "is_deleted": False,
    })
    return str(result.inserted_id)


async def make_address(db, owner_id: str, *, pincode: str = "452099") -> str:
    n = _n()
    result = await db.addresses.insert_one({
        "owner_id": owner_id,
        "label": "Test",
        "line1": f"Test Street {n}",
        "city": "Indore",
        "state": "MP",
        "pincode": pincode,
        "is_default": True,
        "is_deleted": False,
    })
    return str(result.inserted_id)


async def make_customer_with_vehicle(db, vehicle_type_id: str, *, pincode: str = "452099", phone_verified: bool = True) -> tuple[str, str, str]:
    """The common case: a customer + one default vehicle + one default
    address, ready to book with. Returns (customer_id, vehicle_id, address_id)."""
    customer_id = await make_customer(db, phone_verified=phone_verified)
    vehicle_id = await make_vehicle(db, customer_id, vehicle_type_id)
    address_id = await make_address(db, customer_id, pincode=pincode)
    return customer_id, vehicle_id, address_id


async def make_captain(db, service_center_id: str | None = None, *, wallet_balance: float = 200.0) -> str:
    n = _n()
    result = await db.users.insert_one({
        "full_name": f"Test Captain {n}",
        "email": f"test.captain.{n}@example.com",
        "phone": f"8{n:09d}",
        "password_hash": hash_password("Test@12345"),
        "role": "captain",
        "status": "active",
        "service_center_id": service_center_id,
        "is_deleted": False,
    })
    captain_id = str(result.inserted_id)
    await db.captain_wallets.insert_one({
        "captain_id": captain_id,
        "balance": wallet_balance,
        "minimum_balance": 50.0,
        "is_deleted": False,
    })
    return captain_id


async def make_manager(db, service_center_id: str) -> str:
    n = _n()
    result = await db.users.insert_one({
        "full_name": f"Test Manager {n}",
        "email": f"test.manager.{n}@example.com",
        "phone": f"7{n:09d}",
        "password_hash": hash_password("Test@12345"),
        "role": "manager",
        "status": "active",
        "service_center_id": service_center_id,
        "is_deleted": False,
    })
    return str(result.inserted_id)


async def get_hatchback_type_id(db) -> str:
    doc = await db.vehicle_types.find_one({"slug": "hatchback"})
    assert doc, "seed() should have created the 'hatchback' vehicle type"
    return str(doc["_id"])


async def get_suv_type_id(db) -> str:
    doc = await db.vehicle_types.find_one({"slug": "suv"}) or await db.vehicle_types.find_one({"is_deleted": {"$ne": True}, "slug": {"$ne": "hatchback"}})
    assert doc, "seed() should have created at least one non-hatchback vehicle type"
    return str(doc["_id"])


async def get_star_wash_service_id(db) -> str:
    """The default car service the seed creates (foam wash + vacuum + dashboard)."""
    doc = await db.services.find_one({"slug": "star-wash"})
    assert doc, "seed() should have created the 'star-wash' service"
    return str(doc["_id"])


async def get_any_active_plan(db) -> str:
    doc = await db.subscription_plans.find_one({"is_deleted": {"$ne": True}, "is_active": True})
    assert doc, "seed() should have created at least one active subscription plan"
    return str(doc["_id"])


async def make_subscription_plan(
    db,
    *,
    vehicle_types: list[str],
    included_service_ids: list[str] | None = None,
    total_service_count: int = 4,
    vehicle_type_prices: dict[str, float] | None = None,
) -> str:
    """seed()'s own demo plans are all deliberately unrestricted
    (vehicle_types=[] — "every vehicle type eligible"), so any test that
    needs to exercise a REAL vehicle-type restriction needs its own plan,
    not a seeded one."""
    n = _n()
    result = await db.subscription_plans.insert_one({
        "name": f"Test Plan {n}",
        "slug": f"test-plan-{n}",
        "description": "Test-only plan",
        "billing_cycle": "monthly",
        "price": 499.0,
        "discounted_price": None,
        "vehicle_type_prices": vehicle_type_prices or {},
        "vehicle_type_discounted_prices": {},
        "included_service_ids": included_service_ids or [],
        "category_quotas": {},
        "total_service_count": total_service_count,
        "vehicle_types": vehicle_types,
        "upgrade_to_plan_ids": [],
        "is_active": True,
        "is_popular": False,
        "display_order": 0,
        "is_deleted": False,
    })
    return str(result.inserted_id)
