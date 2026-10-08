"""
Final whole-system security pass (2026-10).

- Manager bookings land in the manager's OWN center only: the center is
  resolved from the address and compared with the manager's DB record —
  an address served by another center is refused before anything (a
  customer profile, an address, a booking) is written.
- A captain created by a manager with a password the manager chose must
  replace it on first login (must_change_password), and can — through the
  same /auth/set-password gate customers use.
"""
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def centers(db, cleanup):
    center_a = await make_service_center(db, pincode="452731")
    center_b = await make_service_center(db, pincode="452742")
    manager_a = await make_manager(db, center_a)
    unlinked = await make_manager(db, None)
    customer = await make_customer(db)
    cleanup.extend([
        ("service_centers", {"_id": {"$in": [ObjectId(center_a), ObjectId(center_b)]}}),
        ("users", {"_id": {"$in": [ObjectId(x) for x in (manager_a, unlinked, customer)]}}),
        ("addresses", {"owner_id": customer}),
        ("bookings", {"customer_id": customer}),
    ])
    return {
        "center_a": center_a, "center_b": center_b, "manager_a": manager_a, "unlinked": unlinked, "customer": customer,
        "hatchback": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db),
    }


async def test_manager_quick_booking_into_another_centers_area_is_refused(db, centers, cleanup):
    phone = "9444400071"
    cleanup.append(("users", {"phone": phone}))
    body = {
        "customer_name": "Cross Center", "customer_phone": phone,
        "address": {"line1": "12 Other Center Road", "pincode": "452742"},
        "lines": [{"vehicle_type": centers["hatchback"], "quantity": 1, "service_ids": [centers["star"]]}],
        "scheduled_date": "2099-01-01", "scheduled_slot": "09:00-12:00", "payment_method": "cash",
    }
    async with _client() as client:
        res = await client.post("/api/v1/bookings/manager-quick", json=body, headers=_auth(centers["manager_a"], "manager", centers["center_a"]))
        assert res.status_code == 403 and "another service center" in res.json()["message"]
        # A manager with no center linked books nowhere.
        res = await client.post(
            "/api/v1/bookings/manager-quick", json={**body, "address": {"line1": "1 Own Road", "pincode": "452731"}},
            headers=_auth(centers["unlinked"], "manager", None),
        )
        assert res.status_code == 403
    # Refused before the customer profile was even created.
    assert not await db.users.find_one({"phone": phone})


async def test_manager_create_with_a_new_address_in_another_centers_area_is_refused(db, centers):
    body = {
        "customer_id": centers["customer"], "vehicle_type": centers["hatchback"], "service_ids": [centers["star"]],
        "new_address": {"line1": "9 Other Center Lane", "city": "Indore", "state": "MP", "pincode": "452742"},
        "scheduled_date": "2099-01-01T00:00:00", "scheduled_slot": "09:00-12:00", "payment_method": "cash",
    }
    async with _client() as client:
        res = await client.post("/api/v1/bookings/manager-create", json=body, headers=_auth(centers["manager_a"], "manager", centers["center_a"]))
    assert res.status_code == 403
    # Nothing written — not even the new address.
    assert await db.addresses.count_documents({"owner_id": centers["customer"]}) == 0
    assert await db.bookings.count_documents({"customer_id": centers["customer"]}) == 0


async def test_log_a_done_job_is_manager_only_and_files_under_their_own_center(db, centers):
    """The admin has no center of their own — 'log a done job' is the
    manager's tool (the admin's center view hides it)."""
    admin = await db.users.find_one({"role": "admin"})
    async with _client() as client:
        res = await client.post("/api/v1/bookings/manager-log-completed", json={}, headers=_auth(str(admin["_id"]), "admin"))
    assert res.status_code == 403


async def test_staff_created_captain_must_change_the_password_on_first_login(db, centers, cleanup):
    phone = "9444400072"
    cleanup.append(("users", {"phone": phone}))
    async with _client() as client:
        res = await client.post(
            "/api/v1/auth/staff",
            json={"full_name": "New Captain", "phone": phone, "password": "Chosen@123", "role": "captain"},
            headers=_auth(centers["manager_a"], "manager", centers["center_a"]),
        )
        assert res.status_code == 200, res.text
        created = res.json()["data"]
        assert created["must_change_password"] is True and created["service_center_id"] == centers["center_a"]

        login = await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Chosen@123"})
        assert login.status_code == 200, login.text
        data = login.json()["data"]
        assert data["user"]["must_change_password"] is True
        headers = {"Authorization": f"Bearer {data['access_token']}"}

        # Whoever chose the temporary password (the manager) logs in with it
        # too — that session must not survive the captain's own password.
        creator = await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Chosen@123"})
        creator_refresh = creator.json()["data"]["refresh_token"]

        # The gate the captain app shows: set a password without the old one.
        res = await client.post("/api/v1/auth/set-password", json={"new_password": "MyOwn@4567"}, headers=headers)
        assert res.status_code == 200, res.text
        fresh = {"Authorization": f"Bearer {res.json()['data']['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=fresh)
        assert me.status_code == 200 and me.json()["data"]["must_change_password"] is False
        # Every earlier session is gone — access and refresh alike.
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
        assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": creator_refresh})).status_code == 401
        # ...and only once — afterwards the normal change-password applies.
        again = await client.post("/api/v1/auth/set-password", json={"new_password": "Another@890"}, headers=fresh)
        assert again.status_code == 400

        assert (await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Chosen@123"})).status_code == 401
        assert (await client.post("/api/v1/auth/login", json={"identifier": phone, "password": "MyOwn@4567"})).status_code == 200


async def test_a_phoneless_signed_in_customer_must_prove_the_number_they_book_with(db, centers, cleanup):
    """A Google sign-in has no phone. Booking used to attach whatever number
    was typed — with no OTP — so anyone could claim a stranger's
    not-yet-registered number (their later bookings / OTP logins then
    landed in the claimer's account)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    res = await db.users.insert_one({
        "full_name": "Google Person", "email": f"g.{ObjectId()}@example.com", "role": "customer", "status": "active",
        "is_deleted": False, "created_at": now, "updated_at": now, "account_source": "google",
    })
    cleanup.append(("users", {"_id": res.inserted_id}))
    victim_phone = "9444400073"
    body = {
        "customer_name": "Google Person", "customer_phone": victim_phone,
        "address": {"line1": "12 Own Center Road", "pincode": "452731"},
        "lines": [{"vehicle_type": centers["hatchback"], "quantity": 1, "service_ids": [centers["star"]]}],
        "scheduled_date": "2099-01-01", "scheduled_slot": "09:00-12:00", "payment_method": "cash",
    }
    async with _client() as client:
        refused = await client.post("/api/v1/bookings/quick", json=body, headers=_auth(str(res.inserted_id), "customer"))
        assert refused.status_code == 400 and "verify" in refused.json()["message"].lower()
        wrong = await client.post("/api/v1/bookings/quick", json={**body, "phone_otp": "000000"}, headers=_auth(str(res.inserted_id), "customer"))
        assert wrong.status_code == 400
    assert not (await db.users.find_one({"_id": res.inserted_id})).get("phone")
    assert not await db.users.find_one({"phone": victim_phone})
