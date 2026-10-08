"""
"Admin in a manager's view" hardening (2026-10 admin pass).

The admin panel renders the manager's booking queue for any center
(AdminBookingsPage -> BookingQueuePage centerIdOverride). That is only safe
if the SERVER is the boundary: a manager's center comes from their DB user
record, never from a path/query/body param, and every center-scoped
endpoint refuses another center's data. These tests walk every booking /
queue / KPI / subscriber / captain endpoint a manager can reach with
manager A's token aimed at center B's ids, plus the fail-closed cases
(a manager with no center linked), and pin that admin actions taken in a
center's queue land in the audit trail attributed to that center.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.utils.timezone import now_ist
from tests.factories import (
    get_hatchback_type_id,
    get_star_wash_service_id,
    make_address,
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


def _booking(customer_id: str, center_id: str, number: str, **extra) -> dict:
    now = datetime.now(timezone.utc)
    when = now_ist() + timedelta(days=2)
    return {
        "booking_number": number, "customer_id": customer_id, "service_center_id": center_id,
        "status": "pending", "payment_status": "pending", "payment_method": "cash",
        "total_amount": 400, "subtotal": 400, "scheduled_date": when.replace(hour=0, minute=0, second=0, microsecond=0),
        "scheduled_slot": "09:00-12:00", "service_ids": [], "customer_phone": "9000000001",
        "created_at": now, "updated_at": now, "is_deleted": False, "priority": "medium", "source": "staff",
        "visit_line_key": f"line-{number}",
        **extra,
    }


@pytest.fixture
async def rig(db, cleanup):
    center_a = await make_service_center(db, pincode="452711")
    center_b = await make_service_center(db, pincode="452722")
    manager_a = await make_manager(db, center_a)
    manager_b = await make_manager(db, center_b)
    unlinked_manager = await make_manager(db, None)
    captain_a = await make_captain(db, center_a)
    captain_b = await make_captain(db, center_b)
    loose_captain = await make_captain(db, None)
    customer = await make_customer(db)
    admin = await db.users.find_one({"role": "admin"})
    service_id = await get_star_wash_service_id(db)
    hatchback = await get_hatchback_type_id(db)
    # Registered BEFORE the inserts: a fixture that dies half-way must not
    # leave rows that collide with the next test's unique booking numbers.
    cleanup.append(("bookings", {"booking_number": {"$in": ["BK-SCOPE-A1", "BK-SCOPE-B1"]}}))
    cleanup.append(("payment_orders", {"razorpay_link_id": "plink_test_scope"}))

    booking_a = (await db.bookings.insert_one(_booking(customer, center_a, "BK-SCOPE-A1", service_ids=[service_id], vehicle_type=hatchback))).inserted_id
    booking_b = (await db.bookings.insert_one(_booking(
        customer, center_b, "BK-SCOPE-B1", service_ids=[service_id], vehicle_type=hatchback,
        captain_issue_flag="captain_not_reached",
    ))).inserted_id
    complaint_b = (await db.complaints.insert_one({
        "customer_id": customer, "booking_id": str(booking_b), "service_center_id": center_b,
        "subject": "Late", "description": "Captain was late", "category": "delay", "status": "open",
        "priority": "medium", "replies": [], "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })).inserted_id
    review_b = (await db.reviews.insert_one({
        "booking_id": str(booking_b), "customer_id": customer, "captain_id": captain_b, "rating": 2,
        "comment": "B-center review text", "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })).inserted_id
    sub_b = (await db.user_subscriptions.insert_one({
        "customer_id": customer, "plan_id": str(ObjectId()), "service_center_id": center_b, "status": "active",
        "amount_paid": 999, "remaining_service_count": 3, "total_service_count": 4,
        "start_date": datetime.now(timezone.utc), "end_date": datetime.now(timezone.utc) + timedelta(days=20),
        "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })).inserted_id
    offer_b = (await db.payment_orders.insert_one({
        "kind": "link", "purpose": "subscription", "status": "created", "channel": "manager",
        "customer_id": customer, "service_center_id": center_b, "amount_paise": 99900,
        "razorpay_link_id": "plink_test_scope", "created_at": now_ist(),
    })).inserted_id
    leave_b = (await db.leave_requests.insert_one({
        "captain_id": loose_captain, "start_date": "2026-12-01", "end_date": "2026-12-02",
        "reason": "family", "status": "pending", "created_at": datetime.now(timezone.utc), "is_deleted": False,
    })).inserted_id
    address_b = await make_address(db, customer, pincode="452722")

    cleanup.extend([
        ("bookings", {"_id": {"$in": [booking_a, booking_b]}}),
        ("complaints", {"_id": complaint_b}),
        ("reviews", {"_id": review_b}),
        ("user_subscriptions", {"_id": sub_b}),
        ("payment_orders", {"_id": offer_b}),
        ("leave_requests", {"_id": leave_b}),
        ("addresses", {"owner_id": customer}),
        ("audit_logs", {"target_id": {"$in": [str(booking_a), str(booking_b)]}}),
        ("captain_wallets", {"captain_id": {"$in": [captain_a, captain_b, loose_captain]}}),
        ("users", {"_id": {"$in": [ObjectId(x) for x in (manager_a, manager_b, unlinked_manager, captain_a, captain_b, loose_captain, customer)]}}),
        ("service_centers", {"_id": {"$in": [ObjectId(center_a), ObjectId(center_b)]}}),
    ])
    return {
        "center_a": center_a, "center_b": center_b, "manager_a": manager_a, "manager_b": manager_b,
        "unlinked_manager": unlinked_manager, "captain_a": captain_a, "captain_b": captain_b,
        "loose_captain": loose_captain, "customer": customer, "admin": str(admin["_id"]),
        "booking_a": str(booking_a), "booking_b": str(booking_b), "complaint_b": str(complaint_b),
        "sub_b": str(sub_b), "offer_b": str(offer_b), "leave_b": str(leave_b), "address_b": address_b,
        "service_id": service_id,
    }


def _cross_center_requests(r: dict) -> list[tuple[str, str, dict | None]]:
    """Every manager-reachable read/write, aimed at center B's ids."""
    b, cb = r["booking_b"], r["center_b"]
    return [
        ("GET", f"/api/v1/bookings/center/{cb}", None),
        ("GET", f"/api/v1/bookings/center/{cb}/subscribers", None),
        ("GET", f"/api/v1/bookings/{b}", None),
        ("GET", f"/api/v1/bookings/{b}/eligible-captains", None),
        ("POST", f"/api/v1/bookings/{b}/assign-captain", {"captain_id": r["captain_a"]}),
        ("POST", f"/api/v1/bookings/{b}/self-assign", None),
        ("POST", f"/api/v1/bookings/{b}/mark-done", {}),
        ("POST", f"/api/v1/bookings/{b}/resolve-issue", {"note": "called the captain"}),
        ("PATCH", f"/api/v1/bookings/{b}/priority", {"priority": "high"}),
        ("PATCH", f"/api/v1/bookings/{b}/details", {"customer_notes": "x"}),
        ("POST", f"/api/v1/bookings/{b}/cancel", {"reason": "customer asked"}),
        ("GET", f"/api/v1/analytics/manager-summary/{cb}", None),
        ("GET", f"/api/v1/analytics/kpis/manager-overview/{cb}?period=30d", None),
        ("GET", f"/api/v1/analytics/manager-dashboard/{cb}?period=30d", None),
        ("GET", f"/api/v1/subscriptions/center/{cb}/overview", None),
        ("GET", f"/api/v1/subscriptions/center/{cb}/plan-purchases?period=30d", None),
        ("GET", f"/api/v1/subscriptions/{r['sub_b']}/usage", None),
        ("POST", f"/api/v1/subscriptions/manager-offers/{r['offer_b']}/void", None),
        ("GET", f"/api/v1/staff/captains/center/{cb}", None),
        ("GET", f"/api/v1/staff/captains/center/{cb}/performance", None),
        ("GET", f"/api/v1/staff/captains/{r['captain_b']}/kyc", None),
        ("GET", f"/api/v1/staff/captains/{r['captain_b']}/locations", None),
        ("GET", f"/api/v1/staff/captains/{r['captain_b']}/attendance", None),
        ("GET", f"/api/v1/wallet/captain/{r['captain_b']}", None),
        ("GET", f"/api/v1/payments/collections/center/{cb}", None),
        ("GET", f"/api/v1/complaints/center/{cb}", None),
        ("GET", f"/api/v1/complaints/{r['complaint_b']}", None),
        ("PUT", f"/api/v1/complaints/{r['complaint_b']}", {"status": "resolved"}),
        ("GET", f"/api/v1/reviews/center/{cb}", None),
        ("GET", f"/api/v1/reviews/booking/{b}", None),
        ("GET", f"/api/v1/leave-requests/center/{cb}", None),
        ("GET", f"/api/v1/inventory/center/{cb}", None),
        ("GET", f"/api/v1/service-centers/{cb}/slot-capacity?date={(now_ist() + timedelta(days=2)).date().isoformat()}", None),
        ("GET", f"/api/v1/service-centers/{cb}/capacity-policy", None),
        # Admin-only reports a manager must not reach at all.
        ("GET", "/api/v1/analytics/kpis/business?period=30d", None),
        ("GET", "/api/v1/analytics/kpis-explorer?period=30d", None),
        ("GET", f"/api/v1/bookings?service_center_id={cb}", None),
        ("GET", "/api/v1/audit-logs", None),
    ]


async def _call(client, method, url, body, headers):
    if method == "GET":
        return await client.get(url, headers=headers)
    return await client.request(method, url, json=body, headers=headers)


@pytest.mark.asyncio
async def test_manager_a_cannot_read_or_act_on_center_b_anywhere(rig, db):
    headers = _auth(rig["manager_a"], "manager", rig["center_a"])
    before = await db.bookings.find_one({"_id": ObjectId(rig["booking_b"])})
    async with _client() as client:
        leaks = []
        for method, url, body in _cross_center_requests(rig):
            res = await _call(client, method, url, body, headers)
            if res.status_code not in (403, 404):
                leaks.append(f"{method} {url} -> {res.status_code}")
    assert not leaks, "manager A reached center B: " + "; ".join(leaks)

    # Nothing about B's booking / offer changed through the refused writes.
    after = await db.bookings.find_one({"_id": ObjectId(rig["booking_b"])})
    for field in ("status", "captain_id", "priority", "customer_notes", "captain_issue_flag"):
        assert after.get(field) == before.get(field), field
    offer = await db.payment_orders.find_one({"_id": ObjectId(rig["offer_b"])})
    assert offer["status"] == "created"


@pytest.mark.asyncio
async def test_manager_center_comes_from_db_not_from_the_token_or_params(rig, db):
    """A token minted with center B in its claims for manager A (whose DB
    row says A) is refused outright — the claim is never trusted."""
    forged = _auth(rig["manager_a"], "manager", rig["center_b"])
    async with _client() as client:
        res = await client.get(f"/api/v1/bookings/center/{rig['center_b']}", headers=forged)
    assert res.status_code == 401

    # And the positive control: their own center works.
    async with _client() as client:
        res = await client.get(f"/api/v1/bookings/center/{rig['center_a']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
    assert res.status_code == 200
    assert {b["booking_number"] for b in res.json()["data"]} == {"BK-SCOPE-A1"}


@pytest.mark.asyncio
async def test_customer_subscription_reads_hide_other_centers_grants(rig):
    async with _client() as client:
        res_a = await client.get(f"/api/v1/subscriptions/customer/{rig['customer']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        res_b = await client.get(f"/api/v1/subscriptions/customer/{rig['customer']}", headers=_auth(rig["manager_b"], "manager", rig["center_b"]))
        res_admin = await client.get(f"/api/v1/subscriptions/customer/{rig['customer']}", headers=_auth(rig["admin"], "admin"))
    ids = lambda res: {s["id"] for s in res.json()["data"]}  # noqa: E731
    assert rig["sub_b"] not in ids(res_a)
    assert rig["sub_b"] in ids(res_b)
    assert rig["sub_b"] in ids(res_admin)


@pytest.mark.asyncio
async def test_unlinked_manager_fails_closed(rig):
    """A manager with no center linked used to 'match' every center-less
    object (None == None) and get platform-wide breakdowns."""
    headers = _auth(rig["unlinked_manager"], "manager", None)
    async with _client() as client:
        for url in (
            f"/api/v1/staff/captains/{rig['loose_captain']}/kyc",
            f"/api/v1/wallet/captain/{rig['loose_captain']}",
            f"/api/v1/staff/captains/{rig['loose_captain']}/locations",
            "/api/v1/analytics/vehicle-types",
            "/api/v1/analytics/services",
        ):
            res = await client.get(url, headers=headers)
            assert res.status_code in (403, 404), f"{url} -> {res.status_code}"
        res = await client.put(
            f"/api/v1/leave-requests/{rig['leave_b']}/review", json={"status": "approved"}, headers=_auth(rig["manager_a"], "manager", rig["center_a"]),
        )
        assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_manager_cannot_book_into_another_centers_area(rig):
    from app.controllers.booking_controller import BookingController
    from app.core.dependencies import CurrentUser
    from app.core.exceptions import ForbiddenException
    from app.core.database import get_database

    controller = BookingController(get_database())
    manager_a = CurrentUser(id=rig["manager_a"], role="manager", service_center_id=rig["center_a"])
    with pytest.raises(ForbiddenException):
        await controller._ensure_manager_books_own_center(manager_a, address_id=rig["address_b"])
    # The admin may book anywhere; manager B may book their own area.
    await controller._ensure_manager_books_own_center(CurrentUser(id=rig["admin"], role="admin"), address_id=rig["address_b"])
    await controller._ensure_manager_books_own_center(
        CurrentUser(id=rig["manager_b"], role="manager", service_center_id=rig["center_b"]), address_id=rig["address_b"],
    )


@pytest.mark.asyncio
async def test_admin_actions_in_a_centers_queue_are_audited_with_that_center(rig, db):
    """The admin works center B's queue 'as its manager' — every action is
    on the audit trail as actor=admin, attributed to center B (resolved
    from the booking itself), and filterable as admin-in-center."""
    headers = _auth(rig["admin"], "admin")
    async with _client() as client:
        res = await client.post(f"/api/v1/bookings/{rig['booking_b']}/resolve-issue", json={"note": "spoke to the captain"}, headers=headers)
        assert res.status_code == 200, res.text
        res = await client.patch(f"/api/v1/bookings/{rig['booking_b']}/priority", json={"priority": "high"}, headers=headers)
        assert res.status_code == 200, res.text

        entries = await db.audit_logs.find({"target_id": rig["booking_b"], "actor_id": rig["admin"]}).to_list(length=20)
        actions = {e["action"] for e in entries}
        assert {"RESOLVE_BOOKING_ISSUE", "UPDATE_BOOKING_PRIORITY"} <= actions
        for e in entries:
            assert e["actor_role"] == "admin"
            assert e["service_center_id"] == rig["center_b"]
            assert e["admin_in_center"] is True
            assert e.get("target_label") == "BK-SCOPE-B1"

        # The audit page can pull exactly "what admins did in center B".
        res = await client.get(
            f"/api/v1/audit-logs?service_center_id={rig['center_b']}&admin_in_center=true&page_size=50", headers=headers,
        )
        assert res.status_code == 200
        rows = res.json()["data"]
        assert {"RESOLVE_BOOKING_ISSUE", "UPDATE_BOOKING_PRIORITY"} <= {r["action"] for r in rows}
        assert all(r["service_center_id"] == rig["center_b"] for r in rows)
        assert any(r.get("service_center_name") for r in rows)

    # A manager's own action carries their center but is not "admin in center".
    async with _client() as client:
        res = await client.patch(
            f"/api/v1/bookings/{rig['booking_a']}/priority", json={"priority": "high"}, headers=_auth(rig["manager_a"], "manager", rig["center_a"]),
        )
        assert res.status_code == 200, res.text
    entry = await db.audit_logs.find_one({"target_id": rig["booking_a"], "action": "UPDATE_BOOKING_PRIORITY"})
    assert entry["service_center_id"] == rig["center_a"]
    assert "admin_in_center" not in entry
