"""
Admin "Delete" on a user account is a REAL delete — the row itself must
be gone from the database afterward, not just hidden behind an
is_deleted flag (every other record in this app soft-deletes; accounts
are the deliberate exception, per the founder's own instruction).

Still guards what it always guarded: an account with live work in flight
can't be deleted out from under a booking in progress.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, UnauthorizedException
from app.core.security import create_refresh_token
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from app.services.user_service import UserService
from app.schemas.booking_schema import BookingCreateRequest

from tests.factories import get_hatchback_type_id, make_customer_with_vehicle, make_service_center, make_captain

pytestmark = pytest.mark.asyncio


async def test_delete_user_removes_the_row_permanently(db, cleanup):
    customer_id, _vehicle_id, _address_id = await make_customer_with_vehicle(db, await get_hatchback_type_id(db))
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("vehicles", {"owner_id": customer_id}))
    cleanup.append(("addresses", {"owner_id": customer_id}))

    deleted = await UserService(db).delete_user(customer_id)
    assert deleted is True

    # Not soft-deleted (is_deleted=true, row still there) — actually GONE.
    raw = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert raw is None
    raw_ignoring_soft_delete = await db.users.find_one({"_id": ObjectId(customer_id)}, {"is_deleted": 0})
    assert raw_ignoring_soft_delete is None


async def test_delete_user_refuses_while_a_booking_is_active(db, cleanup):
    hatchback = await get_hatchback_type_id(db)
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, hatchback)
    for coll, q in [("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
                    ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
                    ("slot_capacity", {"service_center_id": center_id}), ("daily_capacity", {"service_center_id": center_id})]:
        cleanup.append((coll, q))

    bs = BookingService(db)
    star = await db.services.find_one({"name": "Star Wash", "is_deleted": {"$ne": True}})
    from app.utils.timezone import now_ist
    from datetime import timedelta
    when = (now_ist().date() + timedelta(days=2)).isoformat()
    slots = await bs.available_slots(center_id, when)
    slot = next(s["key"] for s in slots if s["status"] == "available")
    await bs.create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[str(star["_id"])],
        scheduled_date=when, scheduled_slot=slot, payment_method="cash",
    ))

    with pytest.raises(BadRequestException, match="active booking"):
        await UserService(db).delete_user(customer_id)

    # The row is untouched — refusing must not partially delete anything.
    still_there = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert still_there is not None


async def test_a_deleted_accounts_refresh_token_is_rejected(db, cleanup):
    customer_id = None
    from tests.factories import make_customer
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))

    refresh = create_refresh_token(customer_id, "customer", token_version=0)
    await UserService(db).delete_user(customer_id)

    with pytest.raises(UnauthorizedException, match="no longer exists"):
        await AuthService(db).refresh(refresh)


async def test_deleting_a_captain_with_no_wallet_row_still_works(db, cleanup):
    """Captains carry a separate wallet document with no cross-reference
    back to prevent deletion — the guard is specifically about ACTIVE
    BOOKINGS, not incidental related records."""
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))

    deleted = await UserService(db).delete_user(captain_id)
    assert deleted is True
    assert await db.users.find_one({"_id": ObjectId(captain_id)}) is None
