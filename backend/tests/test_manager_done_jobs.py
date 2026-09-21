"""
Jobs the manager did himself (phone-in / walk-in), plus the manager's
new-booking alert:

  - create_manager_logged_visit: a NEW job saved directly as COMPLETED — no
    captain, no photos, no slot capacity, revenue counted on the day it was
    done, and the customer hears ONE thing at most ("service done").
  - manager_mark_done: an EXISTING not-yet-started booking closed by the
    manager; an assigned captain is released and earns nothing.
  - every new booking alerts each manager of the center (admins if none).
"""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException
from app.models.enums import BookingStatus
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    ManagerLogBookingRequest,
    QuickAddress,
    QuickBookingLine,
    QuickBookingRequest,
)
from app.schemas.review_schema import ReviewCreateRequest
from app.services.analytics_service import AnalyticsService
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.review_service import ReviewService
from app.utils.timezone import from_stored
from tests.conftest import cleanup, db  # noqa: F401 — fixtures
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_captain, make_manager, make_service_center


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db, pincode="452066")
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    await db.service_centers.update_one({"_id": ObjectId(center_id)}, {"$set": {"manager_id": manager_id}})
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("notifications", {"user_id": manager_id}))
    return {
        "db": db, "center_id": center_id, "manager_id": manager_id,
        "hatchback": await get_hatchback_type_id(db), "star": await get_star_wash_service_id(db),
    }


def _track(cleanup, phone: str):
    cleanup.append(("users", {"phone": phone}))
    cleanup.append(("addresses", {"line1": {"$regex": "^Logged Lane"}}))
    cleanup.append(("bookings", {"customer_phone": phone}))
    cleanup.append(("booking_status_history", {"note": {"$regex": "manager"}}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("audit_logs", {"module": "bookings", "action": {"$regex": "^MANAGER_"}}))


def _log(rig, phone: str, **overrides) -> ManagerLogBookingRequest:
    payload = dict(
        customer_name="Walk In Wanda",
        customer_phone=phone,
        lines=[QuickBookingLine(vehicle_type=rig["hatchback"], quantity=1, service_ids=[rig["star"]])],
        scheduled_date=_day(-1),
        service_time="10:30",
        address_line="Logged Lane 7, Vijay Nagar",
        send_whatsapp=True,
    )
    payload.update(overrides)
    return ManagerLogBookingRequest(**payload)


async def _outbox(db, phone: str) -> list[dict]:
    return await db.whatsapp_outbox.find({"phone": phone}).to_list(None)


async def _create_open_booking(rig, cleanup, phone: str, quantity: int = 1, day: int = 1) -> dict:
    """An ordinary website/phone booking waiting in the manager's queue."""
    _track(cleanup, phone)
    customer = await AuthService(rig["db"]).ensure_customer_by_phone(phone, "Queue Quentin")
    cleanup.append(("addresses", {"line1": "12 Queue Lane"}))
    return await BookingService(rig["db"]).create_quick_booking(
        QuickBookingRequest(
            customer_name="Queue Quentin", customer_phone=phone,
            address=QuickAddress(line1="12 Queue Lane, Indore", pincode="452066"),
            lines=[QuickBookingLine(vehicle_type=rig["hatchback"], quantity=quantity, service_ids=[rig["star"]])],
            scheduled_date=_day(day), scheduled_slot="09:00-12:00",
        ),
        customer=customer, source="staff", allow_pinless=True, notify_background=False,
    )


# --------------------------------------------------------------------- log


@pytest.mark.asyncio
async def test_logged_job_is_saved_done_with_no_captain_no_seat_and_paid_cash(rig, cleanup):
    phone = "9666600001"
    _track(cleanup, phone)
    db = rig["db"]
    result = await BookingService(db).create_manager_logged_visit(
        _log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"]
    )
    b = result["bookings"][0]
    assert b["status"] == "completed"
    assert not b.get("captain_id")
    assert b["completed_by_role"] == "manager" and b["completed_by_id"] == rig["manager_id"]
    assert b["payment_status"] == "paid" and b["payment_method"] == "cash"
    assert b["cash_collected_by"] == rig["manager_id"]
    assert b["captain_earning"] == 0 and b["platform_earning"] == b["total_amount"] > 0
    assert b["wallet_settled"] is True
    assert b["service_center_id"] == rig["center_id"]
    assert b["scheduled_slot"] == "09:00-12:00"
    assert b["booking_group_id"] is None

    # Revenue reads closed_at, counts read created_at: both are the SERVICE moment.
    raw = await db.bookings.find_one({"_id": ObjectId(b["id"])})
    for field in ("completed_at", "closed_at", "created_at"):
        assert from_stored(raw[field]).strftime("%Y-%m-%d %H:%M") == f"{_day(-1)} 10:30"

    # No capacity was taken, and history carries the manager as the actor.
    assert await db.slot_capacity.count_documents({"service_center_id": rig["center_id"], "date": _day(-1)}) == 0
    history = await db.booking_status_history.find({"booking_id": b["id"]}).to_list(None)
    assert [h["status"] for h in history] == ["completed"] and history[0]["changed_by"] == rig["manager_id"]
    # Customer profile + address created from the typed details.
    customer = await db.users.find_one({"phone": phone})
    assert customer and customer["full_name"] == "Walk In Wanda"
    assert (await db.addresses.find_one({"owner_id": str(customer["_id"])}))["line1"].startswith("Logged Lane")


@pytest.mark.asyncio
async def test_switch_on_sends_only_service_done_and_off_sends_nothing(rig, cleanup):
    db = rig["db"]
    on, off = "9666600002", "9666600003"
    _track(cleanup, on)
    _track(cleanup, off)
    bs = BookingService(db)
    await bs.create_manager_logged_visit(_log(rig, on, send_whatsapp=True), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    await bs.create_manager_logged_visit(_log(rig, off, send_whatsapp=False), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])

    sent = await _outbox(db, on)
    assert len(sent) == 1, "exactly one WhatsApp — no confirmation, no captain message"
    assert "Service completed" in sent[0]["message"]
    assert await _outbox(db, off) == []
    # ...but the in-app row still exists for the customer who got no WhatsApp.
    uid = str((await db.users.find_one({"phone": off}))["_id"])
    assert await db.notifications.count_documents({"user_id": uid, "title": "Service completed"}) == 1
    cleanup.append(("notifications", {"user_id": uid}))


@pytest.mark.asyncio
async def test_approved_done_template_gets_exactly_the_params_it_declares(rig, cleanup):
    db = rig["db"]
    phone = "9666600004"
    _track(cleanup, phone)
    await db.whatsapp_templates.update_one(
        {"name": "blussit_service_completed_v5"},
        {"$set": {"name": "blussit_service_completed_v5", "status": "APPROVED", "category": "UTILITY", "param_count": 5, "has_url_param": True, "disabled": False}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": "blussit_service_completed_v5"}))
    await BookingService(db).create_manager_logged_visit(_log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    row = (await _outbox(db, phone))[0]
    assert row["template_name"] == "blussit_service_completed_v5"
    # 6 params are built at the call site (incl. the service code) — only 5 are sent.
    assert row["message"].split("] ", 1)[1].count(", ") == 4
    assert "?review=1" in row["message"]  # the "Rate Now" button


@pytest.mark.asyncio
async def test_two_cars_log_as_one_visit_with_one_message(rig, cleanup):
    db = rig["db"]
    phone = "9666600005"
    _track(cleanup, phone)
    result = await BookingService(db).create_manager_logged_visit(
        _log(rig, phone, lines=[QuickBookingLine(vehicle_type=rig["hatchback"], quantity=2, service_ids=[rig["star"]])]),
        manager_id=rig["manager_id"], manager_center_id=rig["center_id"],
    )
    assert result["vehicle_count"] == 2 and result["booking_group_id"]
    assert {b["status"] for b in result["bookings"]} == {"completed"}
    assert len({b["visit_line_key"] for b in result["bookings"]}) == 2
    assert len(await _outbox(db, phone)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("date_offset,time,fragment", [(1, "10:30", "already happened"), (-1, "06:00", "working hours"), (-200, "10:30", "older than")])
async def test_bad_times_are_refused_and_leave_no_customer_behind(rig, cleanup, date_offset, time, fragment):
    db = rig["db"]
    phone = "9666600006"
    _track(cleanup, phone)
    with pytest.raises(BadRequestException) as exc:
        await BookingService(db).create_manager_logged_visit(
            _log(rig, phone, scheduled_date=_day(date_offset), service_time=time),
            manager_id=rig["manager_id"], manager_center_id=rig["center_id"],
        )
    assert fragment in str(exc.value.message if hasattr(exc.value, "message") else exc.value)
    assert await db.users.find_one({"phone": phone}) is None
    assert await db.bookings.count_documents({"customer_phone": phone}) == 0


@pytest.mark.asyncio
async def test_manager_without_a_center_cannot_log(rig, cleanup):
    with pytest.raises(BadRequestException):
        await BookingService(rig["db"]).create_manager_logged_visit(_log(rig, "9666600007"), manager_id=rig["manager_id"], manager_center_id=None)


@pytest.mark.asyncio
async def test_failed_second_car_removes_the_first(rig, cleanup):
    db = rig["db"]
    phone = "9666600008"
    _track(cleanup, phone)
    lines = [
        QuickBookingLine(vehicle_type=rig["hatchback"], quantity=1, service_ids=[rig["star"]]),
        QuickBookingLine(vehicle_type="000000000000000000000000", quantity=1, service_ids=[rig["star"]]),
    ]
    with pytest.raises(Exception):
        await BookingService(db).create_manager_logged_visit(
            _log(rig, phone, lines=lines), manager_id=rig["manager_id"], manager_center_id=rig["center_id"]
        )
    assert await db.bookings.count_documents({"customer_phone": phone}) == 0
    assert await _outbox(db, phone) == []


# --------------------------------------------------------------- dashboards


@pytest.mark.asyncio
async def test_logged_job_shows_in_collections_as_manager_and_not_in_on_time(rig, cleanup):
    db = rig["db"]
    phone = "9666600009"
    _track(cleanup, phone)
    result = await BookingService(db).create_manager_logged_visit(
        _log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"]
    )
    total = result["total_amount"]
    ledger = await PaymentService(db).center_collections(rig["center_id"], "manager", rig["center_id"], None, None)
    row = next(r for r in ledger["rows"] if r["captain_id"] == "manager")
    assert row["captain_name"] == "Done by manager" and row["cash_amount"] == total and row["uncollected_amount"] == 0

    summary = await AnalyticsService(db).manager_summary(rig["center_id"])
    assert summary["on_time_pct"] is None  # no captain start to be "on time" about


# ---------------------------------------------------------------- reviews


@pytest.mark.asyncio
async def test_review_needs_a_captain_rating_only_when_there_was_a_captain(rig, cleanup):
    db = rig["db"]
    phone = "9666600010"
    _track(cleanup, phone)
    cleanup.append(("reviews", {"service_center_id": rig["center_id"]}))
    result = await BookingService(db).create_manager_logged_visit(
        _log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"]
    )
    booking = result["bookings"][0]
    review = await ReviewService(db).create(result["customer_id"], ReviewCreateRequest(booking_id=booking["id"], service_rating=5))
    assert review["captain_id"] is None and review["captain_rating"] is None and review["service_rating"] == 5

    # A booking that did have a captain still demands one.
    phone2 = "9666600011"
    _track(cleanup, phone2)
    other = await BookingService(db).create_manager_logged_visit(_log(rig, phone2), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    await db.bookings.update_one({"_id": ObjectId(other["bookings"][0]["id"])}, {"$set": {"captain_id": "someone"}})
    with pytest.raises(BadRequestException):
        await ReviewService(db).create(other["customer_id"], ReviewCreateRequest(booking_id=other["bookings"][0]["id"], service_rating=4))


# ---------------------------------------------------------------- mark done


@pytest.mark.asyncio
async def test_mark_done_releases_the_captain_pays_nothing_and_sends_one_message(rig, cleanup):
    db = rig["db"]
    phone = "9666600012"
    booking = (await _create_open_booking(rig, cleanup, phone))["bookings"][0]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    bs = BookingService(db)
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=captain_id), rig["manager_id"], "manager", rig["center_id"])
    wallet_before = await db.captain_wallets.find_one({"captain_id": captain_id})
    before = len(await _outbox(db, phone))

    out = await bs.manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=True)
    assert out["completed"] == 1

    done = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert done["status"] == "completed" and done["completed_by_role"] == "manager"
    assert done["captain_id"] is None and captain_id in done["previous_captain_ids"]
    assert done["captain_earning"] == 0 and done["platform_earning"] == done["total_amount"] and done["wallet_settled"] is True
    assert done["payment_status"] == "paid" and done["payment_method"] == "cash" and done["cash_collected_by"] == rig["manager_id"]
    assert done["completed_at"] and done["closed_at"] and done["awaiting_assignment_since"] is None
    assert (await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"] == wallet_before["balance"]
    assert await db.notifications.count_documents({"user_id": captain_id, "title": "Job completed by the manager"}) == 1
    assert len(await _outbox(db, phone)) == before + 1  # only "service done"
    assert (await _outbox(db, phone))[-1]["message"].startswith("Service completed") or "Service completed" in (await _outbox(db, phone))[-1]["message"]

    with pytest.raises(BadRequestException):
        await bs.manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=True)


@pytest.mark.asyncio
async def test_mark_done_with_the_switch_off_is_silent_on_whatsapp(rig, cleanup):
    db = rig["db"]
    phone = "9666600013"
    booking = (await _create_open_booking(rig, cleanup, phone))["bookings"][0]
    before = len(await _outbox(db, phone))
    await BookingService(db).manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=False)
    assert len(await _outbox(db, phone)) == before
    uid = booking["customer_id"]
    assert await db.notifications.count_documents({"user_id": uid, "title": "Service completed"}) == 1
    cleanup.append(("notifications", {"user_id": uid}))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["captain_on_the_way", "service_started", "awaiting_payment", "cancelled"])
async def test_mark_done_refuses_jobs_the_captain_owns_or_that_are_not_real(rig, cleanup, status):
    db = rig["db"]
    booking = (await _create_open_booking(rig, cleanup, "9666600014"))["bookings"][0]
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"status": status}})
    with pytest.raises(BadRequestException):
        await BookingService(db).manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"])
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["status"] == status


@pytest.mark.asyncio
async def test_a_manager_cannot_mark_another_centers_booking(rig, cleanup):
    db = rig["db"]
    booking = (await _create_open_booking(rig, cleanup, "9666600015"))["bookings"][0]
    with pytest.raises(ForbiddenException):
        await BookingService(db).manager_mark_done(booking["id"], "other-manager", "manager", "another-center-id")
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["status"] == "pending"


@pytest.mark.asyncio
async def test_mark_done_closes_the_whole_visit_and_messages_once(rig, cleanup):
    db = rig["db"]
    phone = "9666600016"
    visit = await _create_open_booking(rig, cleanup, phone, quantity=2)
    ids = [b["id"] for b in visit["bookings"]]
    before = len(await _outbox(db, phone))
    out = await BookingService(db).manager_mark_done(ids[0], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=True)
    assert out["completed"] == 2
    for i in ids:
        assert (await db.bookings.find_one({"_id": ObjectId(i)}))["status"] == "completed"
    assert len(await _outbox(db, phone)) == before + 1


@pytest.mark.asyncio
async def test_visit_with_a_car_in_progress_cannot_be_closed_by_the_manager(rig, cleanup):
    db = rig["db"]
    visit = await _create_open_booking(rig, cleanup, "9666600017", quantity=2)
    ids = [b["id"] for b in visit["bookings"]]
    await db.bookings.update_one({"_id": ObjectId(ids[1])}, {"$set": {"status": "service_started"}})
    with pytest.raises(BadRequestException):
        await BookingService(db).manager_mark_done(ids[0], rig["manager_id"], "manager", rig["center_id"])
    assert (await db.bookings.find_one({"_id": ObjectId(ids[0])}))["status"] == "pending"


# ---------------------------------------------------------------- manager alert


@pytest.mark.asyncio
async def test_new_booking_alerts_every_manager_with_the_full_details(rig, cleanup):
    db = rig["db"]
    second = await make_manager(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(second)}))
    cleanup.append(("notifications", {"user_id": second}))
    phone = "9666600018"
    booking = (await _create_open_booking(rig, cleanup, phone))["bookings"][0]

    for manager in (rig["manager_id"], second):
        rows = await db.notifications.find({"user_id": manager, "reference_id": booking["id"]}).to_list(None)
        assert len(rows) == 1, "one alert per manager"
        text = rows[0]["message"]
        for expected in ("Queue Quentin", phone, "Hatchback", "9:00 AM – 12:00 PM", "Queue Lane"):
            assert expected.lower() in text.lower(), f"{expected!r} missing from {text!r}"
        cleanup.append(("whatsapp_outbox", {"phone": (await db.users.find_one({"_id": ObjectId(manager)}))["phone"]}))
        sent = await db.whatsapp_outbox.find({"phone": (await db.users.find_one({"_id": ObjectId(manager)}))["phone"]}).to_list(None)
        assert len(sent) == 1


@pytest.mark.asyncio
async def test_approved_manager_template_gets_six_single_line_params(rig, cleanup):
    db = rig["db"]
    await db.whatsapp_templates.update_one(
        {"name": "blussit_manager_new_booking_v1"},
        {"$set": {"name": "blussit_manager_new_booking_v1", "status": "APPROVED", "category": "UTILITY", "param_count": 6, "has_url_param": False, "disabled": False}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": "blussit_manager_new_booking_v1"}))
    await _create_open_booking(rig, cleanup, "9666600019")
    manager_phone = (await db.users.find_one({"_id": ObjectId(rig["manager_id"])}))["phone"]
    cleanup.append(("whatsapp_outbox", {"phone": manager_phone}))
    row = (await db.whatsapp_outbox.find({"phone": manager_phone}).to_list(None))[-1]
    assert row["template_name"] == "blussit_manager_new_booking_v1"
    assert "\n" not in row["message"]


@pytest.mark.asyncio
async def test_a_center_with_no_manager_falls_back_to_the_admins(rig, cleanup):
    db = rig["db"]
    await db.service_centers.update_one({"_id": ObjectId(rig["center_id"])}, {"$set": {"manager_id": None}})
    await db.users.update_one({"_id": ObjectId(rig["manager_id"])}, {"$set": {"service_center_id": None}})
    booking = (await _create_open_booking(rig, cleanup, "9666600020"))["bookings"][0]
    cleanup.append(("notifications", {"reference_id": booking["id"]}))
    admin_alerts = await db.notifications.find({"reference_id": booking["id"], "message": {"$regex": "needs a captain"}, "title": {"$regex": "^New booking"}}).to_list(None)
    admin_ids = {str(u["_id"]) for u in await db.users.find({"role": "admin"}).to_list(None)}
    assert admin_alerts and {a["user_id"] for a in admin_alerts} <= admin_ids
    for uid in {a["user_id"] for a in admin_alerts}:
        phone = (await db.users.find_one({"_id": ObjectId(uid)})).get("phone")
        if phone:
            cleanup.append(("whatsapp_outbox", {"phone": phone}))


@pytest.mark.asyncio
async def test_no_manager_alert_when_the_manager_logs_his_own_job(rig, cleanup):
    db = rig["db"]
    phone = "9666600021"
    _track(cleanup, phone)
    result = await BookingService(db).create_manager_logged_visit(_log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    assert await db.notifications.count_documents({"user_id": rig["manager_id"], "reference_id": result["bookings"][0]["id"]}) == 0


# ---------------------------------------------------------------- over HTTP


def _auth(user_id: str, role: str, center_id: str | None) -> dict:
    from app.core.security import create_access_token

    token = create_access_token(user_id, role, {"service_center_id": center_id, "tv": 0})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_routes_are_manager_only_and_wired_end_to_end(rig, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    db = rig["db"]
    phone = "9666600030"
    _track(cleanup, phone)
    body = {
        "customer_name": "Http Harry", "customer_phone": phone,
        "lines": [{"vehicle_type": rig["hatchback"], "quantity": 1, "service_ids": [rig["star"]]}],
        "scheduled_date": _day(-1), "service_time": "10:30",
        "address_line": "Logged Lane 9, Palasia", "payment_method": "cash", "send_whatsapp": False,
    }
    manager = _auth(rig["manager_id"], "manager", rig["center_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/bookings/manager-log-completed", json=body)).status_code == 401
        customer_id = str((await AuthService(db).ensure_customer_by_phone("9666600031", "Someone"))["_id"])
        cleanup.append(("users", {"_id": ObjectId(customer_id)}))
        assert (await client.post("/api/v1/bookings/manager-log-completed", json=body, headers=_auth(customer_id, "customer", None))).status_code == 403

        res = await client.post("/api/v1/bookings/manager-log-completed", json=body, headers=manager)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["bookings"][0]["status"] == "completed" and data["vehicle_count"] == 1
        assert await db.audit_logs.count_documents({"action": "MANAGER_LOG_COMPLETED", "target_id": data["bookings"][0]["id"]}) == 1

        # bad input is a clean 400/422, never a 500
        bad = await client.post("/api/v1/bookings/manager-log-completed", json={**body, "service_time": "25:99"}, headers=manager)
        assert bad.status_code == 422
        future = await client.post("/api/v1/bookings/manager-log-completed", json={**body, "scheduled_date": _day(3)}, headers=manager)
        assert future.status_code == 400

        # mark-done: manager only, once
        open_booking = (await _create_open_booking(rig, cleanup, "9666600032"))["bookings"][0]
        url = f"/api/v1/bookings/{open_booking['id']}/mark-done"
        assert (await client.post(url, json={"send_whatsapp": False}, headers=_auth(customer_id, "customer", None))).status_code == 403
        ok = await client.post(url, json={"send_whatsapp": False}, headers=manager)
        assert ok.status_code == 200 and ok.json()["data"]["completed"] == 1
        assert (await client.post(url, json={"send_whatsapp": False}, headers=manager)).status_code == 400
        other = _auth(rig["manager_id"], "manager", rig["center_id"])
        assert (await client.post("/api/v1/bookings/000000000000000000000000/mark-done", json={}, headers=other)).status_code == 404


# ------------------------------------------------------- hardening (review)


@pytest.mark.asyncio
async def test_the_same_job_cannot_be_logged_twice(rig, cleanup):
    db = rig["db"]
    phone = "9666600040"
    _track(cleanup, phone)
    bs = BookingService(db)
    await bs.create_manager_logged_visit(_log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    with pytest.raises(BadRequestException) as exc:
        await bs.create_manager_logged_visit(_log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    assert "already logged" in str(exc.value.message if hasattr(exc.value, "message") else exc.value)
    assert await db.bookings.count_documents({"customer_phone": phone}) == 1
    assert len(await _outbox(db, phone)) == 1  # the customer was told once
    # A different time for the same customer is a different job.
    await bs.create_manager_logged_visit(_log(rig, phone, service_time="12:30"), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    assert await db.bookings.count_documents({"customer_phone": phone}) == 2


@pytest.mark.asyncio
async def test_a_suspended_customer_is_a_400_not_a_401(rig, cleanup):
    db = rig["db"]
    phone = "9666600041"
    _track(cleanup, phone)
    await AuthService(db).ensure_customer_by_phone(phone, "Suspended Sam")
    await db.users.update_one({"phone": phone}, {"$set": {"status": "suspended"}})
    with pytest.raises(BadRequestException):
        await BookingService(db).create_manager_logged_visit(_log(rig, phone), manager_id=rig["manager_id"], manager_center_id=rig["center_id"])
    assert await db.bookings.count_documents({"customer_phone": phone}) == 0


def test_blank_address_and_empty_vehicle_type_are_rejected_by_the_schema():
    from pydantic import ValidationError

    base = dict(
        customer_name="Walk In Wanda", customer_phone="9666600042", scheduled_date="2026-01-01", service_time="10:30",
        lines=[QuickBookingLine(vehicle_type="x", quantity=1, service_ids=["s"])],
    )
    with pytest.raises(ValidationError):
        ManagerLogBookingRequest(**base, address_line="     ")
    with pytest.raises(ValidationError):
        QuickBookingLine(vehicle_type="", quantity=1, service_ids=["s"])
    assert ManagerLogBookingRequest(**base, address_line="  12   Lane  ").address_line == "12 Lane"


@pytest.mark.asyncio
async def test_mark_done_clears_a_stale_assignment_time_and_a_late_release_cannot_reopen_it(rig, cleanup):
    from app.schemas.booking_schema import CaptainCancelRequest

    db = rig["db"]
    phone = "9666600043"
    booking = (await _create_open_booking(rig, cleanup, phone))["bookings"][0]
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("notifications", {"user_id": captain_id}))
    bs = BookingService(db)
    await bs.assign_captain(booking["id"], BookingAssignCaptainRequest(captain_id=captain_id), rig["manager_id"], "manager", rig["center_id"])
    await bs.manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=False)
    done = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    assert done["assigned_at"] is None  # no captain timeline feeds the completion-time averages

    # The captain's release lands a moment too late: it must be refused and
    # must NOT drag the completed booking back to pending.
    await db.bookings.update_one({"_id": ObjectId(booking["id"])}, {"$set": {"captain_id": captain_id}})
    stale = await db.bookings.find_one({"_id": ObjectId(booking["id"])})
    stale["status"] = "assigned"  # what the captain's request had read before the manager acted
    real_find = bs.repo.find_by_id

    async def _stale_read(_id):
        return stale

    bs.repo.find_by_id = _stale_read
    try:
        with pytest.raises(BadRequestException):
            await bs.captain_cancel(booking["id"], CaptainCancelRequest(reason="Vehicle trouble"), captain_id)
    finally:
        bs.repo.find_by_id = real_find
    assert (await db.bookings.find_one({"_id": ObjectId(booking["id"])}))["status"] == "completed"


@pytest.mark.asyncio
async def test_mark_done_route_accepts_an_empty_body(rig, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    booking = (await _create_open_booking(rig, cleanup, "9666600044"))["bookings"][0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/api/v1/bookings/{booking['id']}/mark-done", headers=_auth(rig["manager_id"], "manager", rig["center_id"]))
    assert res.status_code == 200, res.text
    assert res.json()["data"]["completed"] == 1


# ------------------------------------------------- 12-hour IST display


def test_slots_are_shown_on_the_12_hour_clock():
    from app.utils.slots import format_slot_12h, format_time_12h

    assert format_slot_12h("09:00-12:00") == "9:00 AM – 12:00 PM"
    assert format_slot_12h("12:00-15:30") == "12:00 PM – 3:30 PM"
    assert format_slot_12h("00:30-01:00") == "12:30 AM – 1:00 AM"
    assert format_slot_12h("17:00-20:00", compact=True) == "5 PM–8 PM"
    assert format_time_12h("14:05") == "2:05 PM"
    assert format_slot_12h(None) == "" and format_slot_12h("weird") == "weird"


@pytest.mark.asyncio
async def test_customer_and_manager_messages_use_12_hour_slots(rig, cleanup):
    db = rig["db"]
    phone = "9666600045"
    booking = (await _create_open_booking(rig, cleanup, phone))["bookings"][0]
    cleanup.append(("notifications", {"user_id": booking["customer_id"]}))
    _, _, _, wa_slot, _, _ = await BookingService(db)._wa_details(await db.bookings.find_one({"_id": ObjectId(booking["id"])}))
    assert wa_slot == "9:00 AM – 12:00 PM"
    confirmation = await db.notifications.find_one({"user_id": booking["customer_id"], "title": {"$regex": "booked$"}})
    assert confirmation and "9:00 AM – 12:00 PM" in confirmation["message"] and "09:00" not in confirmation["message"]


# ------------------------------------------------ reconciliation / safety


@pytest.mark.asyncio
async def test_mark_done_voids_an_open_payment_link_so_the_customer_cannot_pay_twice(rig, cleanup, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "")  # never call Razorpay from a test
    db = rig["db"]
    booking = (await _create_open_booking(rig, cleanup, "9666600046"))["bookings"][0]
    await db.payment_orders.insert_one({
        "kind": "link", "razorpay_link_id": "plink_test_open", "status": "created", "booking_id": booking["id"],
        "purpose": "booking", "amount_paise": 100, "customer_id": booking["customer_id"],
    })
    cleanup.append(("payment_orders", {"razorpay_link_id": "plink_test_open"}))
    await BookingService(db).manager_mark_done(booking["id"], rig["manager_id"], "manager", rig["center_id"], send_whatsapp=False)
    link = await db.payment_orders.find_one({"razorpay_link_id": "plink_test_open"})
    assert link["status"] == "voided" and link["voided_reason"]
    cleanup.append(("notifications", {"user_id": booking["customer_id"]}))


@pytest.mark.asyncio
async def test_manager_recorded_upi_is_split_out_of_online_for_reconciliation(rig, cleanup):
    db = rig["db"]
    phone = "9666600047"
    _track(cleanup, phone)
    result = await BookingService(db).create_manager_logged_visit(
        _log(rig, phone, payment_method="online"), manager_id=rig["manager_id"], manager_center_id=rig["center_id"]
    )
    total = result["total_amount"]
    ledger = await PaymentService(db).center_collections(rig["center_id"], "manager", rig["center_id"], None, None)
    row = next(r for r in ledger["rows"] if r["captain_id"] == "manager")
    assert row["online_amount"] == total and row["manual_online_amount"] == total and row["cash_amount"] == 0
    assert ledger["totals"]["manual_online_amount"] == total
