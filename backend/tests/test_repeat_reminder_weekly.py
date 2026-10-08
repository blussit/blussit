"""
"Time for a wash?" to existing customers, weekly (2026-09-28):

  - users.last_completed_at is stamped ($max — only ever forward) by every
    completion path: captain after-photo, manager mark-done, manager-logged
    job; a one-time, guarded, batched backfill fills it for older bookings;
  - the finder reads users (role + last_completed_at index), uses the
    admin's repeat_reminder_days as BOTH the lapse and the cooldown (was a
    hard-coded 30), and skips opted-out customers, live bookings and pass
    holders (they get the pass wash reminder instead);
  - the default is weekly (7 days, admin-editable, minimum 3);
  - the WhatsApp side is marketing-only (approved template or nothing);
  - customers can opt out themselves: PUT /users/me {"marketing_opt_out"}.
"""
from datetime import datetime, timedelta, timezone

import pydantic
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app import main
from app.schemas.booking_schema import BookingAssignCaptainRequest, BookingCreateRequest, ManagerLogBookingRequest, PhotoCaptureRequest, QuickBookingLine
from app.schemas.content_schema import BookingPolicyUpdateRequest
from app.services.booking_policy_service import DEFAULT_BOOKING_POLICY
from app.services.booking_service import BookingService, backfill_last_completed_at
from app.services.notification_service import NotificationService
from app.utils.timezone import from_stored, now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_captain,
    make_customer,
    make_customer_with_vehicle,
    make_manager,
    make_service_center,
    make_recorded_photo_url,
    own_upload_url,
)
from tests.test_reminder_loop import _stub_sweeps, lease  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _midnight(days: int) -> datetime:
    return now_ist().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(days=days)


async def test_default_repeat_reminder_is_weekly_and_stays_editable_down_to_3_days():
    assert DEFAULT_BOOKING_POLICY["repeat_reminder_days"] == 7
    assert BookingPolicyUpdateRequest(repeat_reminder_days=3).repeat_reminder_days == 3
    with pytest.raises(pydantic.ValidationError):
        BookingPolicyUpdateRequest(repeat_reminder_days=2)


# --------------------------------------------------- last_completed_at


@pytest.fixture
async def shop(db, cleanup):
    center_id = await make_service_center(db, working_hours_start="00:00", working_hours_end="23:59")
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    captain_id = await make_captain(db, center_id)
    for coll, flt in (
        ("service_centers", {"_id": ObjectId(center_id)}), ("slot_capacity", {"service_center_id": center_id}),
        ("daily_capacity", {"service_center_id": center_id}), ("users", {"_id": ObjectId(manager_id)}),
        ("users", {"_id": ObjectId(captain_id)}), ("captain_wallets", {"captain_id": captain_id}),
        ("notifications", {"user_id": {"$in": [manager_id, captain_id]}}),
    ):
        cleanup.append((coll, flt))
    return {
        "center_id": center_id, "manager_id": manager_id, "captain_id": captain_id,
        "hatchback": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db),
    }


async def _customer_with_booking(db, shop, cleanup) -> tuple[str, dict]:
    customer_id, vehicle_id, address_id = await make_customer_with_vehicle(db, shop["hatchback"])
    for coll, flt in (
        ("users", {"_id": ObjectId(customer_id)}), ("vehicles", {"owner_id": customer_id}),
        ("addresses", {"owner_id": customer_id}), ("bookings", {"customer_id": customer_id}),
        ("notifications", {"user_id": customer_id}),
    ):
        cleanup.append((coll, flt))
    booking = await BookingService(db).create_booking(customer_id, BookingCreateRequest(
        vehicle_id=vehicle_id, address_id=address_id, service_ids=[shop["star"]],
        scheduled_date=now_ist() + timedelta(days=1), scheduled_slot="09:00-12:00",
    ))
    return customer_id, booking


async def _last_completed(db, customer_id: str):
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    return user.get("last_completed_at")


@pytest.fixture
def on_the_bookings_day(monkeypatch):
    """MGR-06: a booking is marked done on (or after) its own day, never
    before — these tests book a day or two ahead, so the manager's
    mark-done happens "on the day"."""
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def test_every_completion_path_moves_last_completed_at_forward(db, shop, cleanup, on_the_bookings_day):
    bs = BookingService(db)

    # 1. The captain's after-photo.
    captain_customer, booking = await _customer_with_booking(db, shop, cleanup)
    assert await _last_completed(db, captain_customer) is None
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=shop["captain_id"]), "system", "admin", None)
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {
        "status": "service_started", "vehicle_verified": True, "heading_at": now_ist(), "service_started_at": now_ist(),
    }})
    before = _utc_now()
    await bs.capture_after_photo_and_complete(
        booking["id"], PhotoCaptureRequest(image_url=await make_recorded_photo_url(db, shop["captain_id"], "after"), latitude=22.7, longitude=75.8), shop["captain_id"]
    )
    stamped = await _last_completed(db, captain_customer)
    assert stamped is not None and stamped.replace(tzinfo=timezone.utc) >= before - timedelta(seconds=1)

    # A job logged afterwards for an EARLIER time never moves it back.
    phone = (await db.users.find_one({"_id": ObjectId(captain_customer)}))["phone"]
    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    await bs.create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Late Log", customer_phone=phone,
            lines=[QuickBookingLine(vehicle_type=shop["hatchback"], quantity=1, service_ids=[shop["star"]])],
            scheduled_date=yesterday, service_time="10:30", address_line="Repeat Lane 1, Indore", send_whatsapp=False,
        ),
        manager_id=shop["manager_id"], manager_center_id=shop["center_id"],
    )
    assert await _last_completed(db, captain_customer) == stamped

    # 2. A manager-logged job, for a customer with no history: its SERVICE time.
    logged_customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(logged_customer)}))
    cleanup.append(("bookings", {"customer_id": logged_customer}))
    cleanup.append(("addresses", {"owner_id": logged_customer}))
    cleanup.append(("notifications", {"user_id": logged_customer}))
    logged_phone = (await db.users.find_one({"_id": ObjectId(logged_customer)}))["phone"]
    await bs.create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Logged Lata", customer_phone=logged_phone,
            lines=[QuickBookingLine(vehicle_type=shop["hatchback"], quantity=1, service_ids=[shop["star"]])],
            scheduled_date=yesterday, service_time="11:15", address_line="Repeat Lane 2, Indore", send_whatsapp=False,
        ),
        manager_id=shop["manager_id"], manager_center_id=shop["center_id"],
    )
    assert from_stored(await _last_completed(db, logged_customer)).strftime("%Y-%m-%d %H:%M") == f"{yesterday} 11:15"

    # 3. The manager marking an open booking done.
    marked_customer, open_booking = await _customer_with_booking(db, shop, cleanup)
    before = _utc_now()
    await bs.manager_mark_done(open_booking["id"], shop["manager_id"], "admin", None, send_whatsapp=False)
    stamped = await _last_completed(db, marked_customer)
    assert stamped is not None and stamped.replace(tzinfo=timezone.utc) >= before - timedelta(seconds=1)


async def test_backfill_is_batched_idempotent_and_runs_once(db, cleanup):
    cleanup.append(("migrations", {"_id": "users_last_completed_at_v1"}))
    await db.migrations.delete_many({"_id": "users_last_completed_at_v1"})
    now = _utc_now()
    lapsed, ahead = await make_customer(db), await make_customer(db)
    for cid in (lapsed, ahead):
        cleanup.append(("users", {"_id": ObjectId(cid)}))
        cleanup.append(("bookings", {"customer_id": cid}))

    async def booking(cid: str, days_ago: int, **fields) -> None:
        await db.bookings.insert_one({
            "customer_id": cid, "status": "completed", "is_deleted": False, "booking_number": f"BF-{ObjectId()}",
            "completed_at": now - timedelta(days=days_ago), **fields,
        })

    await booking(lapsed, 40)
    await booking(lapsed, 12)
    await booking(lapsed, 2, is_deleted=True)       # a deleted job doesn't count
    await booking(lapsed, 1, status="cancelled")    # nor an undone one
    await booking(ahead, 30)
    # `ahead` already has a newer stamp from a live completion — kept.
    await db.users.update_one({"_id": ObjectId(ahead)}, {"$set": {"last_completed_at": now - timedelta(days=3)}})

    assert await backfill_last_completed_at(db, batch_size=1) >= 2  # batch of 1 = many bulk writes
    lapsed_at = (await db.users.find_one({"_id": ObjectId(lapsed)}))["last_completed_at"].replace(tzinfo=timezone.utc)
    assert abs(lapsed_at - (now - timedelta(days=12))) < timedelta(seconds=1)
    ahead_at = (await db.users.find_one({"_id": ObjectId(ahead)}))["last_completed_at"].replace(tzinfo=timezone.utc)
    assert abs(ahead_at - (now - timedelta(days=3))) < timedelta(seconds=1)
    assert await db.migrations.find_one({"_id": "users_last_completed_at_v1"})

    # Guarded: every later boot is a no-op.
    await booking(lapsed, 5)
    assert await backfill_last_completed_at(db) == 0
    again = (await db.users.find_one({"_id": ObjectId(lapsed)}))["last_completed_at"].replace(tzinfo=timezone.utc)
    assert again == lapsed_at


# ---------------------------------------------------------------- finder


async def test_finder_uses_the_setting_as_cooldown_and_skips_who_it_should(db, cleanup):
    now = _utc_now()
    bs = BookingService(db)

    async def customer(completed_days_ago: float, **fields) -> str:
        cid = await make_customer(db)
        cleanup.append(("users", {"_id": ObjectId(cid)}))
        cleanup.append(("bookings", {"customer_id": cid}))
        cleanup.append(("user_subscriptions", {"customer_id": cid}))
        await db.users.update_one({"_id": ObjectId(cid)}, {"$set": {"last_completed_at": now - timedelta(days=completed_days_ago), **fields}})
        return cid

    due = await customer(10)
    reminded_last_week = await customer(20, last_repeat_reminder_at=now - timedelta(days=8))
    skipped = {
        "wash 5 days ago": await customer(5),
        "reminded 3 days ago": await customer(20, last_repeat_reminder_at=now - timedelta(days=3)),
        "opted out": await customer(10, marketing_opt_out=True),
        "suspended": await customer(10, status="suspended"),
    }
    booked = await customer(10)
    await db.bookings.insert_one({"customer_id": booked, "status": "pending", "is_deleted": False, "booking_number": f"RP-{booked}",
                                  "scheduled_date": _midnight(1), "scheduled_slot": "09:00-12:00"})
    skipped["has a wash booked"] = booked
    on_pass = await customer(10)
    await db.user_subscriptions.insert_one({"customer_id": on_pass, "status": "active", "is_deleted": False,
                                            "remaining_service_count": 2, "end_date": now + timedelta(days=10)})
    skipped["holds a live pass"] = on_pass
    never_wash = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(never_wash)}))
    skipped["never had a wash"] = never_wash

    found = {str(u["_id"]) for u in await bs.find_customers_due_repeat_reminder(7, limit=5000)}
    # Reminded 8 days ago: due again on a weekly setting (the old hard-coded
    # 30-day cooldown kept them out for a month).
    assert {due, reminded_last_week} <= found
    for why, cid in skipped.items():
        assert cid not in found, f"nudged a customer who should be skipped: {why}"

    # Once nudged, out for the next 7 days.
    await bs.mark_repeat_reminder_sent(due)
    assert due not in {str(u["_id"]) for u in await bs.find_customers_due_repeat_reminder(7, limit=5000)}


async def test_repeat_nudge_is_marketing_only_and_uses_the_policy_days(db, lease, cleanup, monkeypatch):  # noqa: F811
    cid = await make_customer(db, name="Ravi Repeat")
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    seen_days: list[int] = []

    async def finder(days, *args, **kwargs):
        seen_days.append(days)
        return [await db.users.find_one({"_id": ObjectId(cid)})]

    calls: list[str] = []
    _stub_sweeps(monkeypatch, calls, find_customers_due_repeat_reminder=finder)
    sent: list[dict] = []

    async def notify(self, user_id, title, message, *args, **kwargs):
        sent.append({"user_id": user_id, "title": title, **kwargs})

    monkeypatch.setattr(NotificationService, "notify", notify)
    await main._sweep_once(db, lease)

    assert seen_days == [int((await BookingService(db).policy_service.get_policy())["repeat_reminder_days"])]
    assert len(sent) == 1
    assert sent[0]["user_id"] == cid and sent[0]["wa_event"] == "repeat_booking" and sent[0]["wa_marketing"] is True
    assert sent[0]["wa_params"] == ["Ravi"]
    assert (await db.users.find_one({"_id": ObjectId(cid)})).get("last_repeat_reminder_at") is not None


# --------------------------------------------------------------- opt-out


async def test_customer_can_opt_out_of_marketing_via_users_me(db, cleanup):
    from app.core.security import create_access_token
    from app.main import app

    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    headers = {"Authorization": f"Bearer {create_access_token(cid, 'customer', {'service_center_id': None, 'tv': 0})}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["data"]["marketing_opt_out"] is False

        off = await client.put("/api/v1/users/me", json={"marketing_opt_out": True}, headers=headers)
        assert off.status_code == 200 and off.json()["data"]["marketing_opt_out"] is True
        assert (await db.users.find_one({"_id": ObjectId(cid)}))["marketing_opt_out"] is True
        assert (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]["marketing_opt_out"] is True

        # A name-only edit leaves the choice alone; false turns it back on.
        await client.put("/api/v1/users/me", json={"full_name": "Still Opted Out"}, headers=headers)
        assert (await db.users.find_one({"_id": ObjectId(cid)}))["marketing_opt_out"] is True
        on = await client.put("/api/v1/users/me", json={"marketing_opt_out": False}, headers=headers)
        assert on.json()["data"]["marketing_opt_out"] is False
