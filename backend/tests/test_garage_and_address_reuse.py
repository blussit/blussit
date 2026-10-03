"""
My Garage (GET /vehicles/garage) and same-place address reuse on booking.

Garage: every car the customer has had washed appears once — one row per
vehicle record, else per plate (case/space-insensitive), else per vehicle
TYPE for plateless bookings — merged with the saved vehicles.

Addresses: a returning customer's booking address that is the same place
(pins within ~60 m, else same normalised line1 + pincode) reuses the record
they already have instead of adding a copy — guest, logged-in and staff.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.booking_schema import ManagerBookingCreateRequest, QuickAddress, QuickBookingLine, QuickBookingRequest
from app.schemas.profile_schema import AddressCreateRequest
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from app.services.garage_service import GarageService, merge_garage
from app.utils.address_match import fill_empty_fields, normalize_address_text, same_place
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, get_suv_type_id, make_customer, make_service_center

# A center far from every other test center / the seed one, so a pin here
# always resolves to it.
C_LAT, C_LNG = 22.8000, 75.9500
PIN_A = "452071"
PIN_B = "452072"
_METRE_LAT = 1 / 111_195  # ~1 m of latitude, in degrees


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ============================================================ address_match


def test_normalised_text_drops_country_pincode_city_and_punctuation():
    drop = ["Indore", "Madhya Pradesh"]
    a = normalize_address_text("12, Test Lane,  Vijay Nagar, Indore, Madhya Pradesh 452001, India", drop=drop)
    b = normalize_address_text("12 Test Lane - Vijay Nagar", drop=drop)
    assert a == b == "12 test lane vijay nagar"
    assert normalize_address_text("India, 452001") == ""


def test_same_pin_is_same_place_and_100m_is_not():
    home = {"line1": "Shared location", "latitude": 22.8, "longitude": 75.95, "pincode": PIN_A}
    near = {"line1": "Totally different geocode label", "latitude": 22.8 + 20 * _METRE_LAT, "longitude": 75.95}
    far = {"line1": "Shared location", "latitude": 22.8 + 100 * _METRE_LAT, "longitude": 75.95, "pincode": PIN_A}
    assert same_place(home, near) is not None and same_place(home, near) < 25
    assert same_place(home, far) is None  # same text, but the pins are 100 m apart


def test_text_match_without_pin_needs_matching_pincode():
    saved = {"line1": "12, Test Lane, Vijay Nagar", "pincode": PIN_A, "city": "Indore", "state": "Madhya Pradesh"}
    same = {"line1": "12 test lane vijay nagar, Indore, Madhya Pradesh 452071, India", "pincode": PIN_A, "latitude": 22.8, "longitude": 75.95}
    other_pin = {"line1": "12 Test Lane, Vijay Nagar", "pincode": PIN_B}
    other_street = {"line1": "14 Test Lane, Vijay Nagar", "pincode": PIN_A}
    assert same_place(saved, same) is not None
    assert same_place(saved, other_pin) is None
    assert same_place(saved, other_street) is None
    # A stand-in pincode ("000000") never vetoes a match.
    assert same_place({**saved, "pincode": "000000"}, other_pin) is not None


def test_fill_only_empty_fields():
    existing = {"line1": "12 Test Lane", "landmark": "", "city": "—", "state": "MP", "pincode": "000000"}
    incoming = {"line1": "12 Test Lane", "landmark": "Near the temple", "city": "Indore", "state": "Madhya Pradesh", "pincode": PIN_A, "latitude": 22.8, "longitude": 75.95}
    assert fill_empty_fields(existing, incoming) == {
        "landmark": "Near the temple", "city": "Indore", "pincode": PIN_A, "latitude": 22.8, "longitude": 75.95,
    }
    filled = {**existing, "landmark": "Gate 2", "city": "Indore", "pincode": PIN_A, "latitude": 1.0, "longitude": 2.0}
    assert fill_empty_fields(filled, incoming) == {}  # nothing filled is ever overwritten


# ============================================================ garage merge (pure)

SUV, SEDAN, HATCH = "t_suv", "t_sedan", "t_hatch"
NAMES = {SUV: "XUV 5-Seater", SEDAN: "Sedan", HATCH: "Hatchback"}


def _group(key, *, vtype, latest, plate=None, vehicle_id=None, washed=None, washes=0, nxt=None, repeat=None):
    return {
        "_id": key, "vehicle_id": vehicle_id, "vehicle_type": vtype, "vehicle_label": NAMES.get(vtype), "plate": plate,
        "latest_at": latest, "last_booking_id": ObjectId(), "last_washed_on": washed, "wash_count": washes,
        "next": nxt, "repeat": repeat,
    }


def _vehicle(vid, vtype, plate, *, default=False, created=datetime(2026, 1, 1)):
    return {"_id": ObjectId(vid), "vehicle_type": vtype, "brand": "Honda", "model": "City", "registration_number": plate, "is_default": default, "created_at": created}


def test_plateless_xuv_washed_twice_is_one_type_row():
    # The aggregation groups plateless bookings by type; two type groups can't
    # exist, but a plateless booking on a since-vanished vehicle record must
    # still fold into the same type row.
    gone = str(ObjectId())
    groups = [
        _group(f"t:{SUV}", vtype=SUV, latest=datetime(2026, 10, 2), washed=datetime(2026, 10, 3), washes=2,
               nxt={"d": datetime(2026, 10, 5), "s": "09:00-12:00", "id": ObjectId()}),
        _group(f"v:{gone}", vtype=SUV, latest=datetime(2026, 9, 1), vehicle_id=gone, washed=datetime(2026, 9, 1), washes=1),
    ]
    rows = merge_garage([], groups, type_names=NAMES)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == f"t:{SUV}" and row["registration_number"] is None and not row["saved"]
    assert row["vehicle_type_name"] == "XUV 5-Seater"
    assert row["last_washed_on"] == "2026-10-03" and row["next_wash_on"] == "2026-10-05" and row["wash_count"] == 3


def test_saved_vehicle_that_was_booked_appears_once_with_its_dates():
    vid = str(ObjectId())
    rep_id = ObjectId()
    groups = [
        _group(f"v:{vid}", vtype=SEDAN, vehicle_id=vid, plate="MP09AB1234", latest=datetime(2026, 10, 1), washed=datetime(2026, 10, 3), washes=1,
               nxt={"d": datetime(2026, 10, 5), "s": "12:00-15:00", "id": ObjectId()},
               repeat={"done": 1, "d": datetime(2026, 10, 3), "s": "09:00-12:00", "id": rep_id}),
        # Plateless sedan washes (quick booking) are that saved sedan too.
        _group(f"t:{SEDAN}", vtype=SEDAN, latest=datetime(2026, 9, 20), washed=datetime(2026, 9, 20), washes=1),
    ]
    rows = merge_garage([_vehicle(vid, SEDAN, "MP09AB1234", default=True)], groups, type_names=NAMES)
    assert len(rows) == 1
    row = rows[0]
    assert row["saved"] and row["vehicle_id"] == vid and row["registration_number"] == "MP09AB1234"
    assert row["last_washed_on"] == "2026-10-03" and row["next_wash_on"] == "2026-10-05" and row["next_wash_slot"] == "12:00-15:00"
    assert row["wash_count"] == 2 and row["repeat_booking_id"] == str(rep_id)


def test_plate_match_is_case_and_space_insensitive():
    vid = str(ObjectId())
    groups = [_group("p:mp09 ab-1234", vtype=SEDAN, plate="mp09 ab-1234", latest=datetime(2026, 10, 1), washes=1, washed=datetime(2026, 10, 1))]
    rows = merge_garage([_vehicle(vid, SEDAN, "MP09AB1234")], groups, type_names=NAMES)
    assert len(rows) == 1 and rows[0]["wash_count"] == 1


def test_plated_history_car_shows_plate_and_type_and_removed_cars_stay_hidden():
    kept, removed = str(ObjectId()), str(ObjectId())
    groups = [
        _group(f"v:{kept}", vtype=HATCH, vehicle_id=kept, plate="MP09ZZ0001", latest=datetime(2026, 9, 10), washes=1, washed=datetime(2026, 9, 10)),
        _group(f"v:{removed}", vtype=SUV, vehicle_id=removed, plate="MP09ZZ0002", latest=datetime(2026, 9, 11), washes=1, washed=datetime(2026, 9, 11)),
    ]
    rows = merge_garage([], groups, type_names=NAMES, removed_vehicle_ids={removed})
    assert [r["id"] for r in rows] == ["p:MP09ZZ0001"]
    assert rows[0]["registration_number"] == "MP09ZZ0001" and rows[0]["vehicle_type_name"] == "Hatchback"
    # ...unless the same plate is saved again — then its history is that car's.
    again = str(ObjectId())
    rows = merge_garage([_vehicle(again, SUV, "MP09 ZZ 0002")], groups, type_names=NAMES, removed_vehicle_ids={removed})
    saved = next(r for r in rows if r["saved"])
    assert saved["vehicle_id"] == again and saved["wash_count"] == 1 and len(rows) == 2


def test_newest_first_and_saved_cars_survive_the_cap():
    vid = str(ObjectId())
    groups = [_group(f"t:type{i}", vtype=f"type{i}", latest=datetime(2026, 9, 1) + timedelta(days=i)) for i in range(5)]
    rows = merge_garage([_vehicle(vid, SEDAN, "MP09AB1234", created=datetime(2025, 1, 1))], groups, type_names=NAMES, limit=3)
    assert len(rows) == 3
    assert rows[0]["id"] == "t:type4" and rows[1]["id"] == "t:type3"
    assert any(r["saved"] for r in rows)


# ============================================================ garage over the DB + HTTP


async def _insert_booking(db, customer_id: str, *, vtype: str, status: str, day: int, slot="09:00-12:00", vehicle_id=None, plate=None, group=None, created=None, services=None) -> str:
    when = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day)
    doc = {
        "customer_id": customer_id, "vehicle_type": vtype, "vehicle_label": "Vehicle", "vehicle_id": vehicle_id,
        "vehicle_registration_number": plate, "status": status, "scheduled_date": when, "scheduled_slot": slot,
        "booking_group_id": group, "address_id": "x", "is_deleted": False, "visit_line_key": str(ObjectId()), "service_ids": services or [],
        "created_at": created or datetime.now(timezone.utc) + timedelta(seconds=day),
    }
    return str((await db.bookings.insert_one(doc)).inserted_id)


@pytest.mark.asyncio
async def test_garage_endpoint_merges_saved_and_washed_cars(db, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    hatch, suv = await get_hatchback_type_id(db), await get_suv_type_id(db)
    customer_id = await make_customer(db)
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    vid = str((await db.vehicles.insert_one({
        "owner_id": customer_id, "vehicle_type": hatch, "brand": "Maruti", "model": "Swift", "registration_number": "MP09GA0001",
        "registration_number_normalized": "MP09GA0001", "is_default": True, "is_deleted": False, "created_at": datetime(2026, 1, 1),
    })).inserted_id)

    star = await get_star_wash_service_id(db)
    # A plateless XUV washed twice, booked again for later — ONE row.
    first = await _insert_booking(db, customer_id, vtype=suv, status="completed", day=-10)
    latest_wash = await _insert_booking(db, customer_id, vtype=suv, status="completed", day=-3)
    upcoming = await _insert_booking(db, customer_id, vtype=suv, status="pending", day=2, slot="12:00-15:00", services=[star])
    # A two-car visit's SUV: counted, but never the one "Clean again" replays.
    await _insert_booking(db, customer_id, vtype=suv, status="completed", day=-1, group="visit-1")
    # The saved hatchback, via a plateless quick booking.
    await _insert_booking(db, customer_id, vtype=hatch, status="completed", day=-5)
    # Cancelled-only history still names the car, but isn't a wash.
    await _insert_booking(db, customer_id, vtype=hatch, status="cancelled", day=-4)

    rows = await GarageService(db).list_for_customer(customer_id)
    assert len(rows) == 2
    xuv = next(r for r in rows if r["vehicle_type"] == suv)
    saved = next(r for r in rows if r["saved"])
    assert xuv["id"] == f"t:{suv}" and xuv["registration_number"] is None and xuv["wash_count"] == 3
    assert xuv["last_washed_on"] == _day(-1) and xuv["next_wash_on"] == _day(2) and xuv["next_booking_id"] == upcoming
    assert xuv["repeat_booking_id"] == latest_wash != first
    # Service names come with the row — no request per car on the page.
    star_name = (await db.services.find_one({"_id": ObjectId(star)}))["name"]
    assert xuv["next_service_name"] == star_name
    assert saved["vehicle_id"] == vid and saved["wash_count"] == 1 and saved["last_washed_on"] == _day(-5)

    token = create_access_token(customer_id, "customer", {"tv": 0})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/vehicles/garage")).status_code == 401
        res = await client.get("/api/v1/vehicles/garage", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert {r["id"] for r in res.json()["data"]} == {f"t:{suv}", f"v:{vid}"}


# ============================================================ address reuse on booking


@pytest.fixture
async def rig(db, cleanup):
    center_a = await make_service_center(db, pincode=PIN_A)
    center_b = await make_service_center(db, pincode=PIN_B)
    for cid in (center_a, center_b):
        await db.service_centers.update_one(
            {"_id": ObjectId(cid)}, {"$set": {"location.latitude": C_LAT, "location.longitude": C_LNG, "location.radius_km": 3.0}}
        )
        cleanup.append(("service_centers", {"_id": ObjectId(cid)}))
    await db.service_centers.update_one({"_id": ObjectId(center_b)}, {"$set": {"location.latitude": None}})  # pincode-only
    return {"db": db, "hatchback": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db)}


def _line(rig, quantity=1):
    return [QuickBookingLine(vehicle_type=rig["hatchback"], quantity=quantity, service_ids=[rig["star"]])]


def _quick(phone: str, rig, address: QuickAddress, day: int, quantity=1) -> QuickBookingRequest:
    return QuickBookingRequest(
        customer_name="Same Place", customer_phone=phone, address=address, lines=_line(rig, quantity),
        scheduled_date=_day(day), scheduled_slot="09:00-12:00",
    )


def _pin(metres_north: float = 0.0, **extra) -> QuickAddress:
    return QuickAddress(line1=extra.pop("line1", "Shared location"), latitude=C_LAT + 0.0005 + metres_north * _METRE_LAT, longitude=C_LNG, pincode=PIN_A, **extra)


async def _live_addresses(db, customer_id: str) -> list[dict]:
    return await db.addresses.find({"owner_id": customer_id, "is_deleted": {"$ne": True}}).to_list(length=None)


def _track(cleanup, customer_id: str):
    cleanup.append(("bookings", {"customer_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))


@pytest.mark.asyncio
async def test_guest_same_pin_reuses_and_100m_away_creates(rig, cleanup):
    db = rig["db"]
    phone = "9555500001"
    bs = BookingService(db)
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Same Place")
    customer_id = str(customer["_id"])
    _track(cleanup, customer_id)

    first = await bs.create_quick_booking(_quick(phone, rig, _pin(0), 1), customer=customer, source="app")
    home_id = first["bookings"][0]["address_id"]
    # Guest again (same phone -> same account): the pin dropped 20 m off,
    # with a different geocode label and a landmark this time.
    again = await AuthService(db).ensure_customer_by_phone(phone, "Same Place")
    second = await bs.create_quick_booking(
        _quick(phone, rig, _pin(20, line1="Near Sapna Sangeeta Road", landmark="Blue gate"), 2), customer=again, source="app"
    )
    assert second["bookings"][0]["address_id"] == home_id
    addresses = await _live_addresses(db, customer_id)
    assert len(addresses) == 1
    assert addresses[0]["landmark"] == "Blue gate" and addresses[0]["line1"] == "Shared location"  # empty filled, filled kept

    # A later landmark never silently overwrites the one on file.
    await bs.create_quick_booking(_quick(phone, rig, _pin(10, landmark="Red gate"), 3), customer=again, source="app")
    assert (await db.addresses.find_one({"_id": ObjectId(home_id)}))["landmark"] == "Blue gate"

    far = await bs.create_quick_booking(_quick(phone, rig, _pin(100), 4), customer=again, source="app")
    assert far["bookings"][0]["address_id"] != home_id
    assert len(await _live_addresses(db, customer_id)) == 2


@pytest.mark.asyncio
async def test_text_equal_without_pin_reuses_and_other_pincode_creates(rig, cleanup):
    db = rig["db"]
    customer_id = await make_customer(db)
    _track(cleanup, customer_id)
    saved = await db.addresses.insert_one({
        "owner_id": customer_id, "label": "Home", "line1": "12, Test Lane, Vijay Nagar", "city": "Indore", "state": "Madhya Pradesh",
        "pincode": PIN_B, "is_default": True, "is_deleted": False, "created_at": datetime.now(timezone.utc),
    })
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    bs = BookingService(db)

    typed = QuickAddress(line1="12 Test Lane Vijay Nagar, Indore, Madhya Pradesh 452072, India", pincode=PIN_B)
    result = await bs.create_quick_booking(_quick(customer["phone"], rig, typed, 1), customer=customer, source="app", allow_pinless=True)
    assert result["bookings"][0]["address_id"] == str(saved.inserted_id)
    assert len(await _live_addresses(db, customer_id)) == 1

    elsewhere = QuickAddress(line1="12, Test Lane, Vijay Nagar", pincode=PIN_A)
    result = await bs.create_quick_booking(_quick(customer["phone"], rig, elsewhere, 2), customer=customer, source="app", allow_pinless=True)
    assert result["bookings"][0]["address_id"] != str(saved.inserted_id)
    assert len(await _live_addresses(db, customer_id)) == 2


@pytest.mark.asyncio
async def test_logged_in_multi_car_visit_shares_the_reused_address(rig, cleanup):
    db = rig["db"]
    customer_id = await make_customer(db)
    _track(cleanup, customer_id)
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    bs = BookingService(db)
    first = await bs.create_quick_booking(_quick(customer["phone"], rig, _pin(0), 1), customer=customer, source="app")
    home_id = first["bookings"][0]["address_id"]

    visit = await bs.create_quick_booking(_quick(customer["phone"], rig, _pin(15), 2, quantity=2), customer=customer, source="app")
    assert visit["vehicle_count"] == 2
    assert {b["address_id"] for b in visit["bookings"]} == {home_id}
    assert len(await _live_addresses(db, customer_id)) == 1


@pytest.mark.asyncio
async def test_staff_bookings_reuse_the_customers_address(rig, cleanup):
    db = rig["db"]
    phone = "9555500004"
    bs = BookingService(db)
    customer = await AuthService(db).ensure_customer_by_phone(phone, "Phone In")
    customer_id = str(customer["_id"])
    _track(cleanup, customer_id)
    first = await bs.create_quick_booking(_quick(phone, rig, _pin(0), 1), customer=customer, source="app")
    home_id = first["bookings"][0]["address_id"]

    # Manager's phone-in quick booking for the existing customer.
    staff = await bs.create_quick_booking(_quick(phone, rig, _pin(30, line1="Typed by manager"), 2), customer=customer, source="staff", allow_pinless=True)
    assert staff["bookings"][0]["address_id"] == home_id

    # The older staff form with an inline new address.
    result = await bs.create_booking_for_customer(
        "staff-actor",
        ManagerBookingCreateRequest(
            customer_id=customer_id, vehicle_type=rig["hatchback"], service_ids=[rig["star"]],
            new_address=AddressCreateRequest(line1="Shared location", city="Indore", state="MP", pincode=PIN_A, latitude=C_LAT + 0.0005, longitude=C_LNG),
            scheduled_date=datetime.strptime(_day(3), "%Y-%m-%d"), scheduled_slot="09:00-12:00",
        ),
    )
    assert result["address_id"] == home_id
    assert len(await _live_addresses(db, customer_id)) == 1


@pytest.mark.asyncio
async def test_address_used_on_a_past_booking_counts_beyond_the_saved_list(rig, cleanup):
    """The saved list is capped (50); an address an earlier booking used
    is still found — the customer who already piled up copies stops growing."""
    db = rig["db"]
    customer_id = await make_customer(db)
    _track(cleanup, customer_id)
    old = datetime.now(timezone.utc) - timedelta(days=400)
    target = await db.addresses.insert_one({
        "owner_id": customer_id, "label": "Home", "line1": "7 Old Road", "city": "Indore", "state": "MP", "pincode": PIN_B,
        "is_default": False, "is_deleted": False, "created_at": old,
    })
    await db.addresses.insert_many([
        {"owner_id": customer_id, "label": "Home", "line1": f"Filler {i}", "city": "Indore", "state": "MP", "pincode": PIN_B,
         "is_default": False, "is_deleted": False, "created_at": datetime.now(timezone.utc) - timedelta(minutes=i)}
        for i in range(55)
    ])
    await db.bookings.insert_one({
        "customer_id": customer_id, "address_id": str(target.inserted_id), "status": "completed", "is_deleted": False,
        "scheduled_date": datetime(2025, 9, 1), "scheduled_slot": "09:00-12:00", "created_at": old,
    })
    customer = await db.users.find_one({"_id": ObjectId(customer_id)})
    result = await BookingService(db).create_quick_booking(
        _quick(customer["phone"], rig, QuickAddress(line1="7, Old Road", pincode=PIN_B), 1), customer=customer, source="staff", allow_pinless=True
    )
    assert result["bookings"][0]["address_id"] == str(target.inserted_id)
    assert len(await _live_addresses(db, customer_id)) == 56
