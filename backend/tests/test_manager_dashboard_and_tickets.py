"""
Manager home + support tickets (2026-10 manager pass).

Tickets: a manager could post ONE update and then "the next one doesn't
work". The server always accepted repeat posts; the staff drawer rendered a
stale copy of the list row that later list refetches overwrote. These pin
the server side of the fix — every post answers with the full, enriched,
current ticket (the drawer now writes exactly that into its cache), GET
/complaints/{id} reads one ticket fresh, a legacy `replies: null` ticket no
longer refuses updates, and the validation answers 4xx (never 500).

Dashboard: GET /analytics/manager-dashboard/{center} is one center only —
another center's manager gets 403 — and its per-service revenue split adds
up exactly to the booking revenue of the period.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ForbiddenException, NotFoundException
from app.schemas.complaint_schema import ComplaintCreateRequest, ComplaintUpdateRequest
from app.services.complaint_service import ComplaintService
from app.services.manager_dashboard_service import ManagerDashboardService
from app.services.kpi_service import resolve_period
from app.utils.timezone import now_ist

from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_captain,
    make_customer,
    make_manager,
    make_service_center,
)


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def rig(db, cleanup):
    """Two centers, a manager each, one customer with a booking at A and a
    ticket about it."""
    center_a = await make_service_center(db)
    center_b = await make_service_center(db)
    manager_a = await make_manager(db, center_a)
    manager_b = await make_manager(db, center_b)
    customer = await make_customer(db)
    admin = await db.users.find_one({"role": "admin"})
    booking = await db.bookings.insert_one({
        "booking_number": f"BKT{ObjectId()}", "customer_id": customer, "service_center_id": center_a,
        "status": "completed", "service_ids": [], "scheduled_date": datetime(2026, 9, 1), "scheduled_slot": "09:00-12:00",
        "total_amount": 0, "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })
    booking_id = str(booking.inserted_id)
    ticket = await ComplaintService(db).create(
        customer, ComplaintCreateRequest(booking_id=booking_id, subject="Seats left dusty", description="Rear seats were not vacuumed.")
    )
    for coll, flt in (
        ("complaints", {"customer_id": customer}),
        ("bookings", {"_id": booking.inserted_id}),
        ("notifications", {"user_id": {"$in": [customer, manager_a, manager_b]}}),
        ("users", {"_id": {"$in": [ObjectId(customer), ObjectId(manager_a), ObjectId(manager_b)]}}),
        ("service_centers", {"_id": {"$in": [ObjectId(center_a), ObjectId(center_b)]}}),
    ):
        cleanup.append((coll, flt))
    return {
        "center_a": center_a, "center_b": center_b, "manager_a": manager_a, "manager_b": manager_b,
        "customer": customer, "admin": str(admin["_id"]), "ticket": ticket["id"], "booking_id": booking_id,
    }


# ---------------------------------------------------------------- tickets


@pytest.mark.asyncio
async def test_manager_posts_update_after_update_and_each_answer_is_the_full_ticket(db, rig):
    cs = ComplaintService(db)
    steps = [("Called the customer, sending a captain back.", "in_progress"), ("Captain re-cleaned the seats.", None), ("Customer happy — closing.", "resolved")]
    for i, (msg, status) in enumerate(steps, 1):
        saved = await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], msg, status)
        # The whole thread so far, in order — not just the newest reply.
        assert [r["message"] for r in saved["replies"]] == [m for m, _ in steps[:i]]
        # Enriched like a list row, so the drawer can render it as-is.
        assert saved["customer_name"]
        assert saved["booking_number"]
        assert saved["replies"][-1]["author_name"]
    assert saved["status"] == "resolved"
    assert saved["resolved_by"] == rig["manager_a"]

    fresh = await cs.get_one(rig["ticket"], rig["manager_a"], "manager", rig["center_a"])
    assert len(fresh["replies"]) == 3 and fresh["status"] == "resolved"
    # Every staff update reached the customer.
    assert await db.notifications.count_documents({"user_id": rig["customer"], "reference_id": rig["ticket"], "title": "Complaint update"}) == 3


@pytest.mark.asyncio
async def test_an_update_after_resolving_still_posts_and_can_reopen(db, rig):
    cs = ComplaintService(db)
    await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], "Done.", "resolved")
    saved = await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], "Customer says it's back — reopening.", "open")
    assert saved["status"] == "open"
    assert len(saved["replies"]) == 2


@pytest.mark.asyncio
async def test_legacy_ticket_with_null_replies_accepts_updates(db, rig):
    await db.complaints.update_one({"_id": ObjectId(rig["ticket"])}, {"$set": {"replies": None}})
    cs = ComplaintService(db)
    first = await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], "First", None)
    second = await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], "Second", None)
    assert [r["message"] for r in first["replies"]] == ["First"]
    assert [r["message"] for r in second["replies"]] == ["First", "Second"]


@pytest.mark.asyncio
async def test_admin_can_update_and_resolve_any_centers_ticket(db, rig):
    cs = ComplaintService(db)
    saved = await cs.add_reply(rig["ticket"], rig["admin"], "admin", None, "Admin looked into it.", "resolved")
    assert saved["status"] == "resolved" and saved["resolved_by"] == rig["admin"]
    again = await cs.update(rig["ticket"], ComplaintUpdateRequest(status="closed"), rig["admin"], actor_role="admin")
    assert again["status"] == "closed"
    assert again["customer_name"]


@pytest.mark.asyncio
async def test_other_centers_manager_cannot_read_update_or_reply(db, rig):
    cs = ComplaintService(db)
    with pytest.raises(ForbiddenException):
        await cs.get_one(rig["ticket"], rig["manager_b"], "manager", rig["center_b"])
    with pytest.raises(ForbiddenException):
        await cs.add_reply(rig["ticket"], rig["manager_b"], "manager", rig["center_b"], "Not mine", None)
    with pytest.raises(ForbiddenException):
        await cs.update(rig["ticket"], ComplaintUpdateRequest(priority="high"), rig["manager_b"], actor_role="manager", actor_center_id=rig["center_b"])


@pytest.mark.asyncio
async def test_ticket_without_a_center_is_admin_only(db, rig):
    await db.complaints.update_one({"_id": ObjectId(rig["ticket"])}, {"$unset": {"service_center_id": ""}})
    cs = ComplaintService(db)
    with pytest.raises(ForbiddenException):
        await cs.add_reply(rig["ticket"], rig["manager_a"], "manager", rig["center_a"], "Mine?", None)
    saved = await cs.add_reply(rig["ticket"], rig["admin"], "admin", None, "Admin handles legacy tickets.", None)
    assert len(saved["replies"]) == 1


@pytest.mark.asyncio
async def test_staff_can_change_status_without_typing_an_update(db, rig):
    """Closing a ticket no longer forces a typed note: the thread records
    the change in plain words and the customer is told. A post with
    neither a note nor a status change is still refused."""
    mgr = _auth(rig["manager_a"], "manager", rig["center_a"])
    async with _client() as client:
        res = await client.post(f"/api/v1/complaints/{rig['ticket']}/reply", json={"status": "resolved"}, headers=mgr)
        assert res.status_code == 200, res.text
        saved = res.json()["data"]
        assert saved["status"] == "resolved" and saved["replies"][-1]["message"] == "Status changed to Resolved."
        nothing = await client.post(f"/api/v1/complaints/{rig['ticket']}/reply", json={}, headers=mgr)
        assert nothing.status_code == 422
        same_status = await client.post(f"/api/v1/complaints/{rig['ticket']}/reply", json={"status": "resolved"}, headers=mgr)
        assert same_status.status_code == 400
        # A customer can't use the shortcut to move their own ticket.
        cust = await client.post(f"/api/v1/complaints/{rig['ticket']}/reply", json={"status": "open"}, headers=_auth(rig["customer"], "customer"))
        assert cust.status_code == 400
    doc = await db.complaints.find_one({"_id": ObjectId(rig["ticket"])})
    assert doc["status"] == "resolved" and doc.get("resolved_at") and len(doc["replies"]) == 1
    assert await db.notifications.count_documents({"user_id": rig["customer"], "title": "Complaint update"}) >= 1


@pytest.mark.asyncio
async def test_customer_reply_on_a_ticket_with_no_center_reaches_the_admins(db, rig, cleanup):
    await db.complaints.update_one({"_id": ObjectId(rig["ticket"])}, {"$unset": {"service_center_id": ""}})
    cleanup.append(("notifications", {"user_id": rig["admin"], "reference_id": rig["ticket"]}))
    cs = ComplaintService(db)
    await cs.add_reply(rig["ticket"], rig["customer"], "customer", None, "Any news?", None)
    assert await db.notifications.count_documents({"user_id": rig["admin"], "reference_id": rig["ticket"]}) == 1
    # ...and the admin's answer reaches the customer.
    before = await db.notifications.count_documents({"user_id": rig["customer"], "reference_id": rig["ticket"]})
    await cs.add_reply(rig["ticket"], rig["admin"], "admin", None, "Looking into it.", None)
    assert await db.notifications.count_documents({"user_id": rig["customer"], "reference_id": rig["ticket"]}) == before + 1


@pytest.mark.asyncio
async def test_customer_reply_reaches_the_centers_managers(db, rig):
    cs = ComplaintService(db)
    await cs.add_reply(rig["ticket"], rig["customer"], "customer", None, "Still dusty.", None)
    assert await db.notifications.count_documents({"user_id": rig["manager_a"], "title": "Customer replied on a complaint"}) == 1
    assert await db.notifications.count_documents({"user_id": rig["manager_b"], "title": "Customer replied on a complaint"}) == 0


@pytest.mark.asyncio
async def test_priority_only_change_does_not_message_the_customer(db, rig):
    cs = ComplaintService(db)
    before = await db.notifications.count_documents({"user_id": rig["customer"], "reference_id": rig["ticket"]})
    await cs.update(rig["ticket"], ComplaintUpdateRequest(priority="urgent"), rig["manager_a"], actor_role="manager", actor_center_id=rig["center_a"])
    assert await db.notifications.count_documents({"user_id": rig["customer"], "reference_id": rig["ticket"]}) == before


@pytest.mark.asyncio
async def test_ticket_http_validation_answers_4xx_not_500(db, rig):
    mgr = _auth(rig["manager_a"], "manager", rig["center_a"])
    tid = rig["ticket"]
    async with _client() as client:
        # Two posts in a row over HTTP both land.
        for text in ("one", "two"):
            ok = await client.post(f"/api/v1/complaints/{tid}/reply", json={"message": text}, headers=mgr)
            assert ok.status_code == 200, ok.text
        assert [r["message"] for r in ok.json()["data"]["replies"]] == ["one", "two"]

        blank = await client.post(f"/api/v1/complaints/{tid}/reply", json={"message": "   "}, headers=mgr)
        assert blank.status_code == 422
        too_long = await client.post(f"/api/v1/complaints/{tid}/reply", json={"message": "x" * 2001}, headers=mgr)
        assert too_long.status_code == 422
        bad_status = await client.post(f"/api/v1/complaints/{tid}/reply", json={"message": "hi", "status": "done"}, headers=mgr)
        assert bad_status.status_code == 422
        bad_id = await client.post("/api/v1/complaints/not-an-id/reply", json={"message": "hi"}, headers=mgr)
        assert bad_id.status_code == 422
        missing = await client.post(f"/api/v1/complaints/{ObjectId()}/reply", json={"message": "hi"}, headers=mgr)
        assert missing.status_code == 404
        empty_put = await client.put(f"/api/v1/complaints/{tid}", json={}, headers=mgr)
        assert empty_put.status_code == 400
        long_note = await client.put(f"/api/v1/complaints/{tid}", json={"resolution_note": "x" * 2001}, headers=mgr)
        assert long_note.status_code == 422
        bad_filter = await client.get(f"/api/v1/complaints/center/{rig['center_a']}?status=whatever", headers=mgr)
        assert bad_filter.status_code == 422

        got = await client.get(f"/api/v1/complaints/{tid}", headers=mgr)
        assert got.status_code == 200 and len(got.json()["data"]["replies"]) == 2
        other_mgr = await client.get(f"/api/v1/complaints/{tid}", headers=_auth(rig["manager_b"], "manager", rig["center_b"]))
        assert other_mgr.status_code == 403
        owner = await client.get(f"/api/v1/complaints/{tid}", headers=_auth(rig["customer"], "customer"))
        assert owner.status_code == 200
        stranger = await client.get(f"/api/v1/complaints/{tid}", headers=_auth(str(ObjectId()), "customer"))
        assert stranger.status_code in (401, 404)


# ---------------------------------------------------------------- dashboard


@pytest.fixture
async def board(db, cleanup):
    center = await make_service_center(db, working_hours_start="08:00", working_hours_end="20:00", slot_duration_minutes=180, default_slot_capacity=4)
    other = await make_service_center(db)
    manager = await make_manager(db, center)
    other_manager = await make_manager(db, other)
    captain_free = await make_captain(db, center)
    captain_busy = await make_captain(db, center)
    hatch = await get_hatchback_type_id(db)
    star = await get_star_wash_service_id(db)
    addon = await db.services.find_one({"is_addon": True, "is_deleted": {"$ne": True}})
    addon_id = str(addon["_id"])
    star_doc = await db.services.find_one({"_id": ObjectId(star)})
    star_price = float((star_doc.get("vehicle_type_prices") or {}).get(hatch, star_doc["price"]))
    addon_price = float((addon.get("vehicle_type_prices") or {}).get(hatch, addon["price"]))

    now = datetime.now(timezone.utc)
    today = datetime.strptime(now_ist().strftime("%Y-%m-%d"), "%Y-%m-%d")
    base = {"customer_id": "c", "vehicle_type": hatch, "vehicle_label": "Hatchback", "scheduled_slot": "08:00-11:00",
            "is_deleted": False, "created_at": now}
    docs = [
        # Completed today at this center: star + add-on, and star alone.
        {**base, "service_center_id": center, "status": "completed", "service_ids": [star, addon_id], "total_amount": 500, "closed_at": now,
         "scheduled_date": today, "booking_number": "DBX1", "captain_id": captain_busy, "payment_method": "cash"},
        {**base, "service_center_id": center, "status": "completed", "service_ids": [star], "total_amount": 349, "closed_at": now,
         "scheduled_date": today, "booking_number": "DBX2", "payment_method": "cash"},
        # On the board today: one needs a captain, one captain is washing now.
        {**base, "service_center_id": center, "status": "pending", "service_ids": [star], "total_amount": 349,
         "scheduled_date": today, "booking_number": "DBX3", "scheduled_slot": "11:00-14:00"},
        {**base, "service_center_id": center, "status": "service_started", "service_ids": [star], "total_amount": 349,
         "scheduled_date": today, "booking_number": "DBX4", "scheduled_slot": "11:00-14:00", "captain_id": captain_busy},
        # Cancelled — never on the board, never revenue.
        {**base, "service_center_id": center, "status": "cancelled", "service_ids": [star], "total_amount": 349,
         "scheduled_date": today, "booking_number": "DBX5"},
        # Another center's completed wash must never leak in.
        {**base, "service_center_id": other, "status": "completed", "service_ids": [star], "total_amount": 999, "closed_at": now,
         "scheduled_date": today, "booking_number": "DBX6"},
    ]
    for i, d in enumerate(docs):
        d["customer_id"] = f"dash-customer-{i}-{center}"
    await db.bookings.insert_many(docs)
    await db.attendance.insert_one({"captain_id": captain_free, "service_center_id": center, "attendance_date": today.strftime("%Y-%m-%d"),
                                    "status": "present", "check_in_time": now_ist().isoformat(), "is_deleted": False})
    for coll, flt in (
        ("bookings", {"booking_number": {"$in": [d["booking_number"] for d in docs]}}),
        ("attendance", {"captain_id": captain_free}),
        ("captain_wallets", {"captain_id": {"$in": [captain_free, captain_busy]}}),
        ("users", {"_id": {"$in": [ObjectId(x) for x in (manager, other_manager, captain_free, captain_busy)]}}),
        ("slot_capacity", {"service_center_id": center}),
        ("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}),
    ):
        cleanup.append((coll, flt))
    return {"center": center, "other": other, "manager": manager, "other_manager": other_manager, "star": star, "addon": addon_id,
            "star_price": star_price, "addon_price": addon_price, "captain_free": captain_free, "captain_busy": captain_busy}


@pytest.mark.asyncio
async def test_dashboard_mix_adds_up_and_stays_in_its_center(db, board):
    s, e, ps, pe = resolve_period("today", None, None)
    data = await ManagerDashboardService(db).dashboard(board["center"], s, e, ps, pe)

    assert data["washes"] == {"washes": 2, "revenue": 849, "plan_washes": 0}
    services = {row["service_id"]: row for row in data["services"]}
    assert services[board["star"]]["washes"] == 2
    assert services[board["addon"]]["washes"] == 1
    # The ₹500 two-service booking is split by list price, in whole rupees,
    # and the per-service revenue adds up to the period's booking revenue.
    star_share = round(500 * board["star_price"] / (board["star_price"] + board["addon_price"]))
    assert services[board["star"]]["revenue"] in (star_share + 349, star_share + 349 - 1, star_share + 349 + 1)
    assert sum(r["revenue"] for r in data["services"]) == 849 == data["sales"]["current"]["revenue"]
    assert [(t["name"], t["washes"], t["revenue"]) for t in data["vehicle_types"]] == [("Hatchback", 2, 849)]


@pytest.mark.asyncio
async def test_dashboard_today_board_and_captains(db, board):
    s, e, ps, pe = resolve_period("today", None, None)
    data = await ManagerDashboardService(db).dashboard(board["center"], s, e, ps, pe)

    slots = {sl["key"]: sl for sl in data["today"]["slots"]}
    assert slots["08:00-11:00"]["cars"] == 2 and slots["08:00-11:00"]["completed"] == 2
    assert slots["11:00-14:00"]["cars"] == 2
    assert slots["11:00-14:00"]["unassigned"] == 1 and slots["11:00-14:00"]["in_progress"] == 1
    assert slots["08:00-11:00"]["label"] == "8:00 AM – 11:00 AM"
    assert data["today"]["cars"] == 4  # the cancelled booking is not on the board
    assert data["today"]["capacity"] == 16  # 4 slots x 4

    states = {c["id"]: c for c in data["captains"]["items"]}
    assert states[board["captain_busy"]]["state"] == "on_job"
    assert states[board["captain_busy"]]["current_booking"] == "DBX4"
    assert states[board["captain_busy"]]["jobs_today"] == 2 and states[board["captain_busy"]]["done_today"] == 1
    assert states[board["captain_free"]]["state"] == "available"
    assert data["captains"]["counts"]["on_job"] == 1 and data["captains"]["counts"]["available"] == 1
    assert data["ops"]["needs_captain"] == 1


@pytest.mark.asyncio
async def test_dashboard_http_is_own_center_only_and_validates(db, board):
    async with _client() as client:
        mine = await client.get(f"/api/v1/analytics/manager-dashboard/{board['center']}?period=7d",
                                headers=_auth(board["manager"], "manager", board["center"]))
        assert mine.status_code == 200, mine.text
        assert set(mine.json()["data"]) >= {"sales", "services", "vehicle_types", "plans", "today", "captains", "ops"}

        theirs = await client.get(f"/api/v1/analytics/manager-dashboard/{board['other']}",
                                  headers=_auth(board["manager"], "manager", board["center"]))
        assert theirs.status_code == 403
        bad_period = await client.get(f"/api/v1/analytics/manager-dashboard/{board['center']}?period=forever",
                                      headers=_auth(board["manager"], "manager", board["center"]))
        assert bad_period.status_code == 422
        bad_id = await client.get("/api/v1/analytics/manager-dashboard/xyz", headers=_auth(board["manager"], "manager", board["center"]))
        assert bad_id.status_code == 422
        bad_dates = await client.get(f"/api/v1/analytics/manager-dashboard/{board['center']}?start=2026-01-01&end=01-02-2026",
                                     headers=_auth(board["manager"], "manager", board["center"]))
        assert bad_dates.status_code == 422
        customer = await client.get(f"/api/v1/analytics/manager-dashboard/{board['center']}", headers=_auth(str(ObjectId()), "customer"))
        assert customer.status_code in (401, 403)
