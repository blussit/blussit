"""
The abandoned-signup recovery + 90-day re-verification rules:
  - booking_access_mode: no account -> register; unverified or stale
    account -> otp (NEVER a password that may not exist); fresh -> password;
  - otp_login proves phone ownership, logs the customer in, and stamps a
    fresh verification (must_change_password preserved so the mandatory
    set-a-password gate still fires);
  - the booking gate rejects STALE verification, not just missing;
  - set_initial_password works only while must_change_password is set.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, PhoneNotVerifiedException
from app.schemas.user_schema import RegisterRequest
from app.services.auth_service import AuthService

from tests.factories import make_customer


def _cleanup_user(cleanup, uid, phone=None):
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    if phone:
        cleanup.append(("otp_requests", {"identifier": phone}))


@pytest.mark.asyncio
async def test_booking_access_modes(db, cleanup):
    auth = AuthService(db)
    # Unknown number -> register.
    assert (await auth.booking_access_mode("9888877701"))["mode"] == "register"

    # Abandoned guest signup: account exists, never verified -> otp.
    result = await auth.register_customer(RegisterRequest(full_name="Abandoned Guest", phone="9888877702", password="Random#12345", guest=True))
    uid = result["user"]["id"]
    _cleanup_user(cleanup, uid, "9888877702")
    assert (await auth.booking_access_mode("9888877702"))["mode"] == "otp"
    user = await db.users.find_one({"_id": ObjectId(uid)})
    assert user["must_change_password"] is True  # guest flag -> forced password setup later

    # Fresh-verified customer -> password.
    verified_id = await make_customer(db)
    fresh = await db.users.find_one({"_id": ObjectId(verified_id)})
    _cleanup_user(cleanup, verified_id)
    assert (await auth.booking_access_mode(fresh["phone"]))["mode"] == "password"

    # Verified long ago (91 days) -> stale -> otp again.
    await db.users.update_one(
        {"_id": ObjectId(verified_id)},
        {"$set": {"phone_verified_at": datetime.now(timezone.utc) - timedelta(days=91)}},
    )
    assert (await auth.booking_access_mode(fresh["phone"]))["mode"] == "otp"


@pytest.mark.asyncio
async def test_otp_login_recovers_the_abandoned_account(db, cleanup):
    auth = AuthService(db)
    phone = "9888877703"
    result = await auth.register_customer(RegisterRequest(full_name="Recovery Case", phone=phone, password="Random#12345", guest=True))
    uid = result["user"]["id"]
    _cleanup_user(cleanup, uid, phone)

    await auth.request_otp(phone, purpose="verification")
    code = (await db.otp_requests.find_one({"identifier": phone}))["otp"]

    with pytest.raises(BadRequestException):
        await auth.otp_login(phone, otp="000000")  # wrong code stays out

    login = await auth.otp_login(phone, otp=code)
    assert login["user"]["id"] == uid
    assert login["access_token"]
    fresh = await db.users.find_one({"_id": ObjectId(uid)})
    assert fresh["phone_verified"] is True and fresh["phone_verified_at"] is not None
    assert fresh["must_change_password"] is True  # gate still owed


@pytest.mark.asyncio
async def test_booking_gate_rejects_stale_verification(db, cleanup):
    from tests.factories import get_hatchback_type_id, make_customer_with_vehicle
    from app.schemas.booking_schema import BookingCreateRequest
    from app.services.booking_service import BookingService

    hatchback = await get_hatchback_type_id(db)
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    _cleanup_user(cleanup, customer_id)
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))
    await db.users.update_one(
        {"_id": ObjectId(customer_id)},
        {"$set": {"phone_verified_at": datetime.now(timezone.utc) - timedelta(days=91)}},
    )
    with pytest.raises(PhoneNotVerifiedException):
        await BookingService(db).create_booking(
            customer_id,
            BookingCreateRequest(vehicle_id=vehicle_id, address_id=address_id, service_ids=[],
                                 scheduled_date=datetime.now(), scheduled_slot="09:00-12:00"),
        )


@pytest.mark.asyncio
async def test_set_initial_password_only_under_the_flag(db, cleanup):
    auth = AuthService(db)
    phone = "9888877704"
    result = await auth.register_customer(RegisterRequest(full_name="Password Setter", phone=phone, password="Random#12345", guest=True))
    uid = result["user"]["id"]
    _cleanup_user(cleanup, uid, phone)

    await auth.set_initial_password(uid, "MyRealPass#1")
    fresh = await db.users.find_one({"_id": ObjectId(uid)})
    assert fresh["must_change_password"] is False
    # Once cleared, the shortcut is closed — normal change-password applies.
    with pytest.raises(BadRequestException, match="current password"):
        await auth.set_initial_password(uid, "Another#Pass1")
    # And the new password really logs in.
    login = await auth.login(phone, "MyRealPass#1")
    assert login["user"]["id"] == uid
