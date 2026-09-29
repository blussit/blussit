"""
Offer terms as admin DATA on a Service (the ₹149 Jet Wash launch offer is
just these set on one row): prepaid_only, charges_travel, offer_tag — plus
the customer distance charge they drive, the staff view of a customer's
saved addresses, and the website-visitor KPI.

Razorpay is always stubbed (never the real API), same as test_payments.py.
"""
import itertools
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import ManagerBookingCreateRequest, ManagerLogBookingRequest, QuickBookingLine, QuickBookingRequest
from app.services import payment_service
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.pricing_service import compute_travel_quote
from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, make_captain, make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio

# Far from every other test center (22.7/75.8), so the nearest-center
# lookup always lands on this one.
CENTER_LAT, CENTER_LNG = 24.2, 78.3
KM_PER_DEG_LAT = 111.195  # haversine, R = 6371 km
STUB_LINK = "https://rzp.io/l/offer-stub"
_seq = itertools.count(1)


class _StubOrders:
    def create(self, payload):
        return {"id": f"order_offer_{next(_seq):06d}", **payload}


class _StubLinks:
    def create(self, payload):
        return {"id": f"plink_offer_{next(_seq):06d}", "short_url": STUB_LINK, **payload}

    def cancel(self, link_id):
        return {"id": link_id, "status": "cancelled"}


class _StubClient:
    order = _StubOrders()
    payment_link = _StubLinks()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


def _day(offset: int) -> str:
    return (now_ist() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _service(db, cleanup, name: str, price: float, **flags) -> str:
    category = await db.categories.find_one({"slug": "car-care"})
    result = await db.services.insert_one({
        "category_id": str(category["_id"]), "name": name, "slug": f"offer-test-{next(_seq)}",
        "vehicle_types": [], "price": price, "discounted_price": None,
        "vehicle_type_prices": {}, "vehicle_type_discounted_prices": {},
        "duration_minutes": 30, "is_addon": False, "is_active": True, "is_deleted": False,
        **flags,
    })
    cleanup.append(("services", {"_id": result.inserted_id}))
    return str(result.inserted_id)


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db, pincode="452188")
    await db.service_centers.update_one(
        {"_id": ObjectId(center_id)},
        {"$set": {"location.latitude": CENTER_LAT, "location.longitude": CENTER_LNG, "location.radius_km": 25.0}},
    )
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("slot_capacity", {"service_center_id": center_id}))
    cleanup.append(("daily_capacity", {"service_center_id": center_id}))
    cleanup.append(("notifications", {"user_id": manager_id}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    return {
        "db": db,
        "center_id": center_id,
        "manager_id": manager_id,
        "hatchback": await get_hatchback_type_id(db),
        "offer": await _service(db, cleanup, "Test Offer Wash", 149.0, prepaid_only=True, charges_travel=True, offer_tag="Special offer"),
        "travel": await _service(db, cleanup, "Test Travel Wash", 300.0, charges_travel=True),
        "plain": await _service(db, cleanup, "Test Plain Wash", 200.0),
    }


async def _customer(rig, cleanup, km: float = 7.4) -> tuple[dict, str]:
    """A customer with one pinned address `km` north of the rig center."""
    db = rig["db"]
    customer_id = await make_customer(db)
    address = await db.addresses.insert_one({
        "owner_id": customer_id, "label": "Home", "line1": f"Offer Street {next(_seq)}", "city": "Indore", "state": "MP",
        "pincode": "452188", "latitude": CENTER_LAT + km / KM_PER_DEG_LAT, "longitude": CENTER_LNG,
        "is_default": True, "is_deleted": False,
    })
    for coll, flt in (
        ("bookings", {"customer_id": customer_id}), ("payment_orders", {"customer_id": customer_id}),
        ("notifications", {"user_id": customer_id}), ("addresses", {"owner_id": customer_id}),
        ("user_subscriptions", {"customer_id": customer_id}), ("users", {"_id": ObjectId(customer_id)}),
    ):
        cleanup.append((coll, flt))
    return await db.users.find_one({"_id": ObjectId(customer_id)}), str(address.inserted_id)


async def _slot(rig, day: str) -> str:
    slots = await BookingService(rig["db"]).available_slots(rig["center_id"], day)
    slot = next((s["key"] for s in slots if s["status"] == "available"), None)
    if slot is None:
        pytest.skip(f"no bookable slot left for {day}")
    return slot


async def _quick(rig, customer: dict, address_id: str, lines: list, *, method="cash", source="app", day=2) -> dict:
    date = _day(day)
    return await BookingService(rig["db"]).create_quick_booking(
        QuickBookingRequest(
            customer_name=customer["full_name"], customer_phone=customer["phone"], address_id=address_id,
            lines=lines, scheduled_date=date, scheduled_slot=await _slot(rig, date), payment_method=method,
        ),
        customer=customer, source=source, allow_pinless=True, notify_background=False,
    )


def _line(rig, key: str, quantity: int = 1) -> QuickBookingLine:
    return QuickBookingLine(vehicle_type=rig["hatchback"], quantity=quantity, service_ids=[rig[key]])


# ----------------------------------------------------------- travel quote


async def test_travel_quote_math():
    assert compute_travel_quote(3.0, 5.0, 2.0)["charge"] == 0  # inside the free km
    assert compute_travel_quote(5.0, 5.0, 2.0)["charge"] == 0
    assert compute_travel_quote(7.0, 5.0, 2.0)["charge"] == 4  # no float drift into ₹5
    assert compute_travel_quote(7.4, 5.0, 2.0)["charge"] == 5  # ₹4.80 -> nearest rupee
    assert compute_travel_quote(8.01, 5.0, 2.0)["charge"] == 6  # ₹6.02: "3 km · ₹6", not ₹7
    assert compute_travel_quote(5.25, 5.0, 2.0)["charge"] == 1  # ₹0.50 rounds half-up
    assert compute_travel_quote(5.1, 5.0, 2.0)["charge"] == 0  # ₹0.20
    assert compute_travel_quote(None, 5.0, 2.0) == {"distance_km": 0.0, "free_km": 5.0, "per_km_rate": 2.0, "charge": 0}


async def test_pricing_config_round_trip_and_partial_put(db, cleanup):
    admin = await db.users.find_one({"role": "admin"})
    headers = _auth(str(admin["_id"]), "admin")
    async with _client() as client:
        try:
            got = (await client.get("/api/v1/pricing-config", headers=headers)).json()["data"]
            assert {"per_km_rate", "default_captain_service_fee", "customer_free_km", "customer_per_km_rate"} <= set(got)

            full = {"per_km_rate": 6.0, "default_captain_service_fee": 45.0, "customer_free_km": 3.0, "customer_per_km_rate": 4.0}
            res = await client.put("/api/v1/pricing-config", json=full, headers=headers)
            assert res.status_code == 200, res.text
            assert {k: res.json()["data"][k] for k in full} == full

            # An old client that only knows the captain fields can't wipe the customer ones.
            res = await client.put("/api/v1/pricing-config", json={"per_km_rate": 7.0, "default_captain_service_fee": 50.0}, headers=headers)
            data = res.json()["data"]
            assert data["per_km_rate"] == 7.0 and data["customer_free_km"] == 3.0 and data["customer_per_km_rate"] == 4.0

            bad = await client.put("/api/v1/pricing-config", json={**full, "customer_per_km_rate": -1}, headers=headers)
            assert bad.status_code == 422
        finally:
            await client.put(
                "/api/v1/pricing-config",
                json={"per_km_rate": 5.0, "default_captain_service_fee": 40.0, "customer_free_km": 5.0, "customer_per_km_rate": 2.0},
                headers=headers,
            )


async def test_coverage_check_returns_the_travel_quote(rig):
    async with _client() as client:
        res = await client.post(
            "/api/v1/service-zones/coverage-check",
            json={"latitude": CENTER_LAT + 7.4 / KM_PER_DEG_LAT, "longitude": CENTER_LNG},
        )
    data = res.json()["data"]
    assert data["covered"] is True and data["center"]["id"] == rig["center_id"]
    assert data["travel"] == {"distance_km": round(data["distance_km"], 2), "free_km": 5.0, "per_km_rate": 2.0, "charge": 5}


async def test_pin_outside_radius_matched_by_pincode_still_gets_its_real_distance(rig):
    # No zones, pin beyond the center's radius: the pincode fallback used to
    # return distance 0, so the visit was never charged (and the captain never paid travel).
    await rig["db"].service_centers.update_one({"_id": ObjectId(rig["center_id"])}, {"$set": {"location.radius_km": 6.0}})
    async with _client() as client:
        res = await client.post(
            "/api/v1/service-zones/coverage-check",
            json={"latitude": CENTER_LAT + 8.0 / KM_PER_DEG_LAT, "longitude": CENTER_LNG, "pincode": "452188"},
        )
    data = res.json()["data"]
    assert data["covered"] is True and data["center"]["id"] == rig["center_id"]
    assert data["distance_km"] == 8.0
    assert data["travel"]["charge"] == 6


# ------------------------------------------------------------ travel charge


async def test_travel_charge_once_per_visit_on_the_first_car(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    # A plain car first, the charges_travel car second: the VISIT pays the
    # trip once, and it lands on the first car.
    result = await _quick(rig, customer, address_id, [_line(rig, "plain"), _line(rig, "travel")])
    assert result["vehicle_count"] == 2 and result["travel_charge"] == 5.0
    first, second = [await rig["db"].bookings.find_one({"_id": ObjectId(b["id"])}) for b in result["bookings"]]
    assert first["group_offset_minutes"] == 0
    assert first["travel_charge"] == 5.0 and 7.3 < first["travel_charge_km"] < 7.5
    assert first["total_amount"] == 205.0  # service price + distance charge
    assert second["travel_charge"] == 0.0 and second["travel_charge_km"] is None and second["total_amount"] == 300.0
    assert result["total_amount"] == 505.0
    # Platform revenue — the captain split is still priced on the service alone.
    assert first["platform_earning"] == round(first["subtotal"] - first["captain_earning"] + 5.0, 2)
    # The road-distance estimate is a different field and stays untouched.
    assert "travel_distance_km" in first
    assert first["status"] == BookingStatus.PENDING.value and first["prepaid_only"] is False


async def test_two_travel_cars_still_pay_one_trip(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    result = await _quick(rig, customer, address_id, [_line(rig, "travel", quantity=2)])
    charges = sorted(b["travel_charge"] for b in result["bookings"])
    assert charges == [0.0, 5.0] and result["total_amount"] == 605.0


async def test_no_travel_charge_without_charges_travel_or_inside_free_km(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    booking = (await _quick(rig, customer, address_id, [_line(rig, "plain")]))["bookings"][0]
    assert booking["travel_charge"] == 0.0 and booking["travel_charge_km"] is None and booking["total_amount"] == 200.0

    near, near_address = await _customer(rig, cleanup, km=3.0)
    booking = (await _quick(rig, near, near_address, [_line(rig, "travel")]))["bookings"][0]
    assert booking["travel_charge"] == 0.0 and booking["total_amount"] == 300.0


async def test_no_travel_charge_when_the_car_is_plan_covered(rig, cleanup):
    db = rig["db"]
    customer, address_id = await _customer(rig, cleanup)
    plan = await db.subscription_plans.find_one({"is_active": True, "is_deleted": {"$ne": True}})
    now = now_ist()
    await db.user_subscriptions.insert_one({
        "customer_id": str(customer["_id"]), "plan_id": str(plan["_id"]), "service_id": rig["travel"],
        "vehicle_type": rig["hatchback"], "status": "active", "remaining_service_count": 3, "total_service_count": 4,
        "start_date": now - timedelta(days=1), "end_date": now + timedelta(days=20),
        "is_deleted": False, "created_at": now, "updated_at": now,
    })
    booking = (await _quick(rig, customer, address_id, [_line(rig, "travel")]))["bookings"][0]
    assert booking["subscription_id"]
    assert booking["travel_charge"] == 0.0 and booking["total_amount"] == 0.0


# ------------------------------------------------------------------ prepaid


async def test_web_prepaid_refuses_cash_and_parks_online(rig, cleanup, gateway):
    customer, address_id = await _customer(rig, cleanup)
    with pytest.raises(BadRequestException, match="Test Offer Wash is prepaid"):
        await _quick(rig, customer, address_id, [_line(rig, "offer")], method="cash")
    assert await rig["db"].bookings.count_documents({"customer_id": str(customer["_id"])}) == 0

    result = await _quick(rig, customer, address_id, [_line(rig, "offer")], method="online")
    booking = result["bookings"][0]
    assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value and booking["prepaid_only"] is True
    assert booking["total_amount"] == 154.0  # ₹149 + the distance charge it also carries
    assert result["awaiting_payment"] is True and result["payment_link"] == STUB_LINK and result["prepaid_only"] is True

    with pytest.raises(BadRequestException, match="prepaid"):
        await BookingService(rig["db"]).switch_to_cash(booking["id"], str(customer["_id"]))
    assert (await rig["db"].bookings.find_one({"_id": ObjectId(booking["id"])}))["status"] == BookingStatus.AWAITING_PAYMENT.value


async def test_a_prepaid_car_makes_the_whole_visit_online_only(rig, cleanup, gateway):
    customer, address_id = await _customer(rig, cleanup)
    result = await _quick(rig, customer, address_id, [_line(rig, "plain"), _line(rig, "offer")], method="online")
    assert all(b["prepaid_only"] and b["status"] == BookingStatus.AWAITING_PAYMENT.value for b in result["bookings"])
    with pytest.raises(BadRequestException, match="prepaid"):
        await BookingService(rig["db"]).switch_group_to_cash(result["booking_group_id"], str(customer["_id"]))


async def test_manager_quick_prepaid_is_parked_with_a_payment_link(rig, cleanup, gateway):
    db = rig["db"]
    customer, address_id = await _customer(rig, cleanup)
    date = _day(2)
    body = {
        "customer_name": customer["full_name"], "customer_phone": customer["phone"], "address_id": address_id,
        "lines": [{"vehicle_type": rig["hatchback"], "quantity": 1, "service_ids": [rig["offer"]]}],
        "scheduled_date": date, "scheduled_slot": await _slot(rig, date), "payment_method": "cash",
    }
    cleanup.append(("audit_logs", {"action": "MANAGER_CREATE_BOOKING", "actor_id": rig["manager_id"]}))
    manager = _auth(rig["manager_id"], "manager", rig["center_id"])
    async with _client() as client:
        refused = await client.post("/api/v1/bookings/manager-quick", json=body, headers=manager)
        assert refused.status_code == 400 and "prepaid" in refused.json()["message"]

        res = await client.post("/api/v1/bookings/manager-quick", json={**body, "payment_method": "online"}, headers=manager)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    booking = data["bookings"][0]
    assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value and booking["prepaid_only"] is True
    assert data["payment_link"] == STUB_LINK
    # The saved address was reused, not copied.
    assert booking["address_id"] == address_id
    assert await db.addresses.count_documents({"owner_id": str(customer["_id"]), "is_deleted": {"$ne": True}}) == 1
    # Parked: the customer is asked to pay, nobody is told it's confirmed.
    notes = await db.notifications.find({"user_id": str(customer["_id"])}).to_list(None)
    assert [n["title"] for n in notes] == ["Finish paying to confirm your booking"]
    assert await db.notifications.count_documents({"user_id": rig["manager_id"], "reference_id": booking["id"]}) == 0


async def test_manager_create_prepaid_is_parked_with_a_payment_link(rig, cleanup, gateway):
    customer, address_id = await _customer(rig, cleanup)
    date = _day(3)
    result = await BookingService(rig["db"]).create_booking_for_customer(
        rig["manager_id"],
        ManagerBookingCreateRequest(
            customer_id=str(customer["_id"]), vehicle_type=rig["hatchback"], address_id=address_id,
            service_ids=[rig["offer"]], scheduled_date=date, scheduled_slot=await _slot(rig, date), payment_method="online",
        ),
    )
    assert result["status"] == BookingStatus.AWAITING_PAYMENT.value and result["payment_link"] == STUB_LINK


async def test_manager_logged_job_is_exempt(rig, cleanup):
    db = rig["db"]
    phone = "9555500101"
    cleanup.append(("bookings", {"customer_phone": phone}))
    cleanup.append(("addresses", {"line1": "Offer Log Lane 1"}))
    cleanup.append(("users", {"phone": phone}))
    result = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Logged Offer", customer_phone=phone, lines=[_line(rig, "offer")],
            scheduled_date=(now_ist() - timedelta(days=1)).strftime("%Y-%m-%d"), service_time="10:30",
            address_line="Offer Log Lane 1", payment_method="cash", send_whatsapp=False,
        ),
        manager_id=rig["manager_id"], manager_center_id=rig["center_id"],
    )
    booking = result["bookings"][0]
    assert booking["status"] == BookingStatus.COMPLETED.value and booking["payment_method"] == "cash"
    assert booking["prepaid_only"] is False and booking["travel_charge"] == 0.0 and booking["total_amount"] == 149.0


async def test_captain_cannot_collect_cash_for_a_prepaid_booking(rig, cleanup):
    db = rig["db"]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    res = await db.bookings.insert_one({
        "booking_number": f"BK-PRE-{next(_seq)}", "customer_id": "c-prepaid", "captain_id": captain_id,
        "service_center_id": rig["center_id"], "status": "completed", "payment_method": "online",
        "payment_status": "pending", "total_amount": 154.0, "prepaid_only": True, "is_deleted": False,
    })
    cleanup.append(("bookings", {"_id": res.inserted_id}))
    with pytest.raises(BadRequestException, match="prepaid"):
        await PaymentService(db).captain_collect_cash(str(res.inserted_id), captain_id)
    assert (await db.bookings.find_one({"_id": res.inserted_id}))["payment_status"] == "pending"


async def test_whatsapp_prepaid_visit_gets_a_link_and_no_cash_button(rig, cleanup, gateway):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    db = rig["db"]
    customer, address_id = await _customer(rig, cleanup)
    phone, customer_id = customer["phone"], str(customer["_id"])
    wa_id = f"91{phone}"
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_conversations", {"wa_id": wa_id}))
    cleanup.append(("slot_holds", {"service_center_id": rig["center_id"]}))
    bot = WhatsAppBotService(db)
    date = _day(2)
    line = {"vehicle_type": rig["hatchback"], "type_name": "Hatchback", "count": 1, "quantity": 1,
            "service_ids": [rig["offer"]], "service_quantities": {}, "service_name": "Test Offer Wash", "subtotal": 149.0}
    data = {"lines": [line], "address_id": address_id, "address_label": "Home", "center_id": rig["center_id"],
            "date": date, "slot": await _slot(rig, date)}

    # The confirm summary already shows the distance charge in the total.
    await bot._hold_and_confirm(wa_id, phone, customer_id, dict(data))
    summary = await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])
    assert "Distance charge" in summary["message"] and "₹154" in summary["message"] and "Prepaid" in summary["message"]

    await bot._on_confirm(wa_id, phone, customer_id, dict(data), "reply", "confirm:yes")
    booking = await db.bookings.find_one({"customer_id": customer_id})
    assert booking["status"] == BookingStatus.AWAITING_PAYMENT.value and booking["payment_method"] == "online"
    sent = await db.whatsapp_outbox.find({"phone": phone}).to_list(None)
    assert STUB_LINK in sent[-1]["message"] and "prepaid" in sent[-1]["message"]
    assert not any(o.get("id", "").startswith("pay:cash") for m in sent for o in m.get("options") or [])
    assert not any("Finish paying" in (m.get("message") or "") for m in sent)  # the bot sends the link itself

    # A stale / typed "cash" tap on the stateless button is refused.
    await bot._on_pay_choice(phone, customer_id, f"pay:cash:{booking['_id']}")
    assert (await db.bookings.find_one({"_id": booking["_id"]}))["status"] == BookingStatus.AWAITING_PAYMENT.value
    reply = await db.whatsapp_outbox.find_one({"phone": phone}, sort=[("_id", -1)])
    assert "prepaid" in reply["message"] and [o["id"] for o in reply["options"]] == [f"pay:online:{booking['_id']}"]


# ----------------------------------------------------------- service fields


async def test_service_offer_fields_round_trip_and_clear(rig, cleanup):
    db = rig["db"]
    admin = await db.users.find_one({"role": "admin"})
    headers = _auth(str(admin["_id"]), "admin")
    category = await db.categories.find_one({"slug": "car-care"})
    cleanup.append(("audit_logs", {"module": "services", "actor_id": str(admin["_id"])}))
    async with _client() as client:
        res = await client.post("/api/v1/services", headers=headers, json={
            "category_id": str(category["_id"]), "name": "Offer Round Trip Wash", "price": 249, "discounted_price": 199,
            "prepaid_only": True, "charges_travel": True, "offer_tag": "  Special offer  ",
        })
        assert res.status_code == 200, res.text
        created = res.json()["data"]
        cleanup.append(("services", {"_id": ObjectId(created["id"])}))
        assert created["prepaid_only"] is True and created["charges_travel"] is True and created["offer_tag"] == "Special offer"

        url = f"/api/v1/services/{created['id']}"
        kept = (await client.put(url, headers=headers, json={"price": 149})).json()["data"]
        assert kept["offer_tag"] == "Special offer" and kept["discounted_price"] == 199 and kept["prepaid_only"] is True

        cleared = (await client.put(url, headers=headers, json={"offer_tag": "", "discounted_price": None, "prepaid_only": False})).json()["data"]
        assert cleared["offer_tag"] is None and cleared["discounted_price"] is None and cleared["prepaid_only"] is False
        assert cleared["price"] == 149 and cleared["charges_travel"] is True

        too_long = await client.put(url, headers=headers, json={"offer_tag": "x" * 31})
        assert too_long.status_code == 422

        # A service saved before these fields existed reads back with defaults.
        legacy = (await client.get(f"/api/v1/services/{rig['plain']}")).json()["data"]
        assert legacy["prepaid_only"] is False and legacy["charges_travel"] is False and legacy["offer_tag"] is None


async def test_seeded_jet_wash_carries_the_launch_offer(db):
    jet = await db.services.find_one({"slug": "jet-wash"})
    car_types = [str(t["_id"]) for t in await db.vehicle_types.find({"slug": {"$ne": "bike"}}).to_list(None)]
    assert jet["price"] == 149.0 and jet["original_price"] == 299.0
    assert all(jet["vehicle_type_prices"][vt] == 149.0 for vt in car_types if vt in jet["vehicle_types"])
    assert set(jet["vehicle_type_prices"]) == set(jet["vehicle_types"])
    assert jet["vehicle_type_original_prices"] and min(jet["vehicle_type_original_prices"].values()) == 299.0
    assert jet["prepaid_only"] is True and jet["charges_travel"] is True and jet["offer_tag"] == "Special offer"


# ------------------------------------------------------ customer addresses


async def test_staff_can_list_a_customers_saved_addresses(rig, cleanup):
    customer, address_id = await _customer(rig, cleanup)
    customer_id = str(customer["_id"])
    async with _client() as client:
        ok = await client.get(f"/api/v1/addresses/customer/{customer_id}", headers=_auth(rig["manager_id"], "manager", rig["center_id"]))
        assert ok.status_code == 200 and [a["id"] for a in ok.json()["data"]] == [address_id]

        forbidden = await client.get(f"/api/v1/addresses/customer/{customer_id}", headers=_auth(customer_id, "customer"))
        assert forbidden.status_code == 403

        manager = _auth(rig["manager_id"], "manager", rig["center_id"])
        not_customer = await client.get(f"/api/v1/addresses/customer/{rig['manager_id']}", headers=manager)
        assert not_customer.status_code == 404
        missing = await client.get("/api/v1/addresses/customer/000000000000000000000000", headers=manager)
        assert missing.status_code == 404


# ------------------------------------------------------------ site visits


async def test_visit_counts_once_per_device_per_ist_day(db, cleanup, monkeypatch):
    from app.services import site_visit_service
    from app.services.site_visit_service import SiteVisitService

    cleanup.append(("site_visits", {"device_id": {"$regex": "^offer-test-"}}))
    svc = SiteVisitService(db)
    assert await svc.record("offer-test-device-1") is True
    assert await svc.record("offer-test-device-1") is False
    assert await svc.record("offer-test-device-2") is True

    tomorrow = now_ist() + timedelta(days=1)
    monkeypatch.setattr(site_visit_service, "now_ist", lambda: tomorrow)
    assert await svc.record("offer-test-device-1") is True

    async with _client() as client:
        monkeypatch.undo()
        first = await client.post("/api/v1/analytics/visit", json={"device_id": "offer-test-http-1"})
        again = await client.post("/api/v1/analytics/visit", json={"device_id": "offer-test-http-1"})
        assert first.json()["data"] == {"counted": True} and again.json()["data"] == {"counted": False}
        for bad in ("short", "has spaces in it", "x" * 65, "bad/char/device"):
            assert (await client.post("/api/v1/analytics/visit", json={"device_id": bad})).status_code == 422


async def test_visitor_stats_shape_and_periods(db, cleanup):
    from app.core.rate_limit import RULES

    cleanup.append(("site_visits", {"device_id": {"$regex": "^offer-stats-"}}))
    await db.site_visits.delete_many({})
    today = now_ist()

    def day(offset: int) -> str:
        return (today + timedelta(days=offset)).strftime("%Y-%m-%d")

    await db.site_visits.insert_many([
        {"device_id": "offer-stats-a", "date": day(0), "created_at": datetime.now()},
        {"device_id": "offer-stats-b", "date": day(0), "created_at": datetime.now()},
        {"device_id": "offer-stats-a", "date": day(-1), "created_at": datetime.now()},
        {"device_id": "offer-stats-a", "date": day(-8), "created_at": datetime.now()},  # previous 7-day period
        {"device_id": "offer-stats-a", "date": day(-20), "created_at": datetime.now()},  # outside both
    ])
    admin = await db.users.find_one({"role": "admin"})
    async with _client() as client:
        res = await client.get("/api/v1/analytics/visitors", params={"period": "7d"}, headers=_auth(str(admin["_id"]), "admin"))
        assert res.status_code == 200, res.text
        stats = res.json()["data"]
        assert (stats["total"], stats["today"], stats["previous_total"]) == (3, 2, 1)
        assert [d["date"] for d in stats["daily"]] == [day(i) for i in range(-6, 1)]
        assert stats["daily"][-1]["visitors"] == 2 and stats["daily"][-2]["visitors"] == 1 and stats["daily"][0]["visitors"] == 0

        custom = (await client.get("/api/v1/analytics/visitors", params={"start": day(-20), "end": day(-20)},
                                   headers=_auth(str(admin["_id"]), "admin"))).json()["data"]
        assert custom["total"] == 1 and len(custom["daily"]) == 1

        customer_id = await make_customer(db)
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        assert (await client.get("/api/v1/analytics/visitors", headers=_auth(customer_id, "customer"))).status_code == 403

    # The public beacon has its own bucket, ahead of the catch-all.
    prefixes = [r[0] for r in RULES]
    assert prefixes.index("/api/v1/analytics/visit") < prefixes.index("/api/v1/")


# ------------------------------------------------- road distance (Google)


class _FakeRoutes:
    """Stands in for Google's Routes API: every route is `meters` long."""

    def __init__(self, meters: int | None = 9600, status: int = 200):
        self.meters, self.status, self.calls = meters, status, 0

    async def post(self, url, headers=None, json=None):
        import httpx

        self.calls += 1
        body = {"routes": [{"distanceMeters": self.meters, "duration": "900s"}]} if self.meters else {"routes": []}
        return httpx.Response(self.status, json=body, request=httpx.Request("POST", url))


@pytest.fixture
async def google_roads(db, monkeypatch):
    from app.core.config import settings
    from app.services import route_service

    fake = _FakeRoutes()
    monkeypatch.setattr(settings, "GOOGLE_MAPS_SERVER_KEY", "test-key")
    monkeypatch.setattr(route_service, "shared_client", lambda name, timeout: fake)
    await db.road_distance_cache.delete_many({})  # earlier tests cached straight-line fallbacks
    yield fake
    await db.road_distance_cache.delete_many({})


async def test_charge_distance_is_by_road_and_asked_once_per_address(db, google_roads):
    from app.services.route_service import charge_road_km

    first = await charge_road_km(db, 22.68, 75.86, 22.74, 75.86)
    again = await charge_road_km(db, 22.68, 75.86, 22.74, 75.86)
    assert first == again == {"km": 9.6, "source": "google"}
    assert google_roads.calls == 1, "the second quote comes from the cache, not Google"
    await charge_road_km(db, 22.68, 75.86, 22.75, 75.86)  # a different address asks again
    assert google_roads.calls == 2


async def test_booking_and_quote_charge_the_road_distance(rig, cleanup, google_roads):
    # 7.4 km in a straight line, 9.6 km by road: (9.6 - 5) x ₹2 = ₹9.20 -> ₹9.
    customer, address_id = await _customer(rig, cleanup)
    address = await rig["db"].addresses.find_one({"_id": ObjectId(address_id)})
    quote = await BookingService(rig["db"]).quote_visit(
        customer_id=str(customer["_id"]), phone=customer["phone"], lines=[_line(rig, "travel")], address=address,
    )
    assert quote["travel"]["distance_km"] == 9.6 and quote["travel_charge"] == 9

    booking = (await _quick(rig, customer, address_id, [_line(rig, "travel")]))["bookings"][0]
    stored = await rig["db"].bookings.find_one({"_id": ObjectId(booking["id"])})
    assert stored["travel_charge"] == 9.0 and stored["travel_charge_km"] == 9.6
    assert stored["travel_charge_source"] == "google"
    assert stored["total_amount"] == 309.0  # exactly what the quote showed

    async with _client() as client:
        res = await client.post(
            "/api/v1/service-zones/coverage-check",
            json={"latitude": address["latitude"], "longitude": address["longitude"]},
        )
    data = res.json()["data"]
    assert data["distance_km"] == 9.6 and data["travel"]["charge"] == 9


async def test_google_down_falls_back_to_straight_line_briefly(db, google_roads):
    from datetime import timezone

    from app.services.route_service import charge_road_km

    google_roads.status, google_roads.meters = 500, None
    lat2 = 22.68 + 7.4 / KM_PER_DEG_LAT
    result = await charge_road_km(db, 22.68, 75.86, lat2, 75.86)
    assert result["source"] == "straight_line" and 7.3 < result["km"] < 7.5
    cached = await db.road_distance_cache.find_one({})
    ttl = cached["expires_at"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    assert ttl.total_seconds() <= 600, "a fallback is only kept for minutes, so Google gets asked again soon"


async def test_pinless_address_keeps_the_straight_line_estimate(rig):
    km, source = await BookingService(rig["db"]).charge_distance_km(
        await rig["db"].service_centers.find_one({"_id": ObjectId(rig["center_id"])}), {"pincode": "452188"}, 3.2,
    )
    assert (km, source) == (3.2, "straight_line")
