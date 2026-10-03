"""
Manager-facing API validation (2026-10 manager pass): bad input is a 4xx at
the door — never a 500 from a strptime / fromisoformat deep inside — and the
manager's own-center scope holds on the new captain-status endpoint.
"""
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from tests.factories import make_captain, make_customer, make_manager, make_service_center


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def team(db, cleanup):
    center = await make_service_center(db)
    other = await make_service_center(db)
    manager = await make_manager(db, center)
    captain = await make_captain(db, center)
    foreign_captain = await make_captain(db, other)
    ids = [manager, captain, foreign_captain]
    cleanup.append(("captain_wallets", {"captain_id": {"$in": [captain, foreign_captain]}}))
    cleanup.append(("inventory", {"service_center_id": center}))
    cleanup.append(("leave_requests", {"captain_id": captain}))
    cleanup.append(("audit_logs", {"target_id": {"$in": ids}}))
    cleanup.append(("users", {"_id": {"$in": [ObjectId(i) for i in ids]}}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(center), ObjectId(other)]}}))
    return {"center": center, "other": other, "manager": manager, "captain": captain, "foreign_captain": foreign_captain,
            "h": _auth(manager, "manager", center)}


@pytest.mark.asyncio
async def test_manager_can_suspend_and_reactivate_own_captain_only(db, team):
    h = team["h"]
    async with _client() as client:
        bad = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "deleted"}, headers=h)
        assert bad.status_code == 422
        before = (await db.users.find_one({"_id": ObjectId(team["captain"])})).get("token_version", 0)
        off = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "suspended"}, headers=h)
        assert off.status_code == 200, off.text
        doc = await db.users.find_one({"_id": ObjectId(team["captain"])})
        assert doc["status"] == "suspended"
        assert doc.get("token_version", 0) == before + 1  # signed out everywhere
        on = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "active"}, headers=h)
        assert on.status_code == 200
        assert (await db.users.find_one({"_id": ObjectId(team["captain"])}))["status"] == "active"

        foreign = await client.post(f"/api/v1/staff/captains/{team['foreign_captain']}/status", json={"status": "suspended"}, headers=h)
        assert foreign.status_code == 403
        not_a_captain = await client.post(f"/api/v1/staff/captains/{team['manager']}/status", json={"status": "suspended"}, headers=h)
        assert not_a_captain.status_code == 404


@pytest.mark.asyncio
async def test_manager_cannot_lift_an_admins_suspension(db, team):
    """An admin suspends a captain (fraud, moonlighting…) — the center's
    manager can't quietly reactivate them; the admin can."""
    from datetime import datetime, timezone

    admin = await db.users.find_one({"role": "admin"})
    admin_h = _auth(str(admin["_id"]), "admin")
    # (the captain factory writes no created_at; the admin route returns the public profile)
    await db.users.update_one({"_id": ObjectId(team["captain"])}, {"$set": {"created_at": datetime.now(timezone.utc)}})
    async with _client() as client:
        res = await client.post(f"/api/v1/users/{team['captain']}/suspend", headers=admin_h)
        assert res.status_code == 200, res.text
        assert (await db.users.find_one({"_id": ObjectId(team["captain"])}))["suspended_by_role"] == "admin"
        refused = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "active"}, headers=team["h"])
        assert refused.status_code == 403
        assert (await db.users.find_one({"_id": ObjectId(team["captain"])}))["status"] == "suspended"
        lifted = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "active"}, headers=admin_h)
        assert lifted.status_code == 200
        doc = await db.users.find_one({"_id": ObjectId(team["captain"])})
        assert doc["status"] == "active" and "suspended_by_role" not in doc
        # A manager's own suspension stays the manager's to lift.
        await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "suspended"}, headers=team["h"])
        assert (await db.users.find_one({"_id": ObjectId(team["captain"])}))["suspended_by_role"] == "manager"
        back = await client.post(f"/api/v1/staff/captains/{team['captain']}/status", json={"status": "active"}, headers=team["h"])
        assert back.status_code == 200


@pytest.mark.asyncio
async def test_leave_review_accepts_only_a_decision(db, team):
    leave = await db.leave_requests.insert_one({
        "captain_id": team["captain"], "start_date": "2026-11-01", "end_date": "2026-11-02", "reason": "Family", "status": "pending", "is_deleted": False,
    })
    lid = str(leave.inserted_id)
    async with _client() as client:
        pending = await client.put(f"/api/v1/leave-requests/{lid}/review", json={"status": "pending"}, headers=team["h"])
        assert pending.status_code == 422
        long_note = await client.put(f"/api/v1/leave-requests/{lid}/review", json={"status": "rejected", "review_note": "x" * 501}, headers=team["h"])
        assert long_note.status_code == 422
        ok = await client.put(f"/api/v1/leave-requests/{lid}/review", json={"status": "approved"}, headers=team["h"])
        assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_inventory_bounds_and_low_stock_beyond_page_one(db, team):
    h = team["h"]
    async with _client() as client:
        neg = await client.post("/api/v1/inventory", json={"service_center_id": team["center"], "item_name": "Foam", "unit": "litre",
                                                            "quantity_available": -1}, headers=h)
        assert neg.status_code == 422
        blank = await client.post("/api/v1/inventory", json={"service_center_id": team["center"], "item_name": "  ", "unit": "litre",
                                                              "quantity_available": 1}, headers=h)
        assert blank.status_code == 422
        bad_center = await client.post("/api/v1/inventory", json={"service_center_id": "nope", "item_name": "Foam", "unit": "litre",
                                                                   "quantity_available": 1}, headers=h)
        assert bad_center.status_code == 422

    # 22 healthy items newest-first, then 3 low ones created FIRST (so they
    # sort last, past page 1 of 20).
    low = [{"service_center_id": team["center"], "item_name": f"Low {i}", "unit": "litre", "quantity_available": 1, "reorder_level": 5,
            "is_deleted": False} for i in range(3)]
    await db.inventory.insert_many(low)
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    await db.inventory.insert_many([
        {"service_center_id": team["center"], "item_name": f"Ok {i}", "unit": "litre", "quantity_available": 50, "reorder_level": 5,
         "is_deleted": False, "created_at": base + timedelta(seconds=i)} for i in range(22)
    ])
    async with _client() as client:
        listed = await client.get(f"/api/v1/inventory/center/{team['center']}?low_stock_only=true&page=1&page_size=20", headers=h)
        assert listed.status_code == 200
        body = listed.json()
        assert body["meta"]["total"] == 3
        assert sorted(i["item_name"] for i in body["data"]) == ["Low 0", "Low 1", "Low 2"]

        item_id = body["data"][0]["id"]
        neg_update = await client.put(f"/api/v1/inventory/{item_id}", json={"quantity_available": -5}, headers=h)
        assert neg_update.status_code == 422


@pytest.mark.asyncio
async def test_queue_and_report_params_are_typed(db, team):
    h, c = team["h"], team["center"]
    async with _client() as client:
        for q in ("status=sleeping", "sort=random", "date_from=03-10-2026", "period=forever", "date_field=paid"):
            r = await client.get(f"/api/v1/bookings/center/{c}?{q}", headers=h)
            assert r.status_code == 422, q
        unknown_scope = await client.get(f"/api/v1/bookings/center/{c}?scope=everything", headers=h)
        assert unknown_scope.status_code == 400
        long_q = await client.get(f"/api/v1/bookings/center/{c}?q={'x' * 101}", headers=h)
        assert long_q.status_code == 422
        ok = await client.get(f"/api/v1/bookings/center/{c}?status=pending&scope=attention&sort=scheduled_asc&date_from=2026-10-01", headers=h)
        assert ok.status_code == 200

        coll = await client.get(f"/api/v1/payments/collections/center/{c}?date_from=yesterday", headers=h)
        assert coll.status_code == 422
        trail = await client.get(f"/api/v1/staff/captains/{team['captain']}/locations?since=not-a-time", headers=h)
        assert trail.status_code == 422
        perf = await client.get(f"/api/v1/staff/captains/{team['captain']}/performance?date_from=2026/10/01", headers=h)
        assert perf.status_code == 422
        perf_center = await client.get(f"/api/v1/staff/captains/center/{c}/performance?date_to=tomorrow", headers=h)
        assert perf_center.status_code == 422


@pytest.mark.asyncio
async def test_profile_and_quick_booking_payloads_are_bounded(db, team):
    customer = await make_customer(db)
    try:
        async with _client() as client:
            short = await client.put("/api/v1/users/me", json={"full_name": "A"}, headers=team["h"])
            assert short.status_code == 422
            script = await client.put("/api/v1/users/me", json={"profile_image": "javascript:alert(1)"}, headers=team["h"])
            assert script.status_code == 422
            good = await client.put("/api/v1/users/me", json={"profile_image": "https://cdn.example.com/p.jpg"}, headers=team["h"])
            assert good.status_code == 200, good.text

            plan_paid = await client.post("/api/v1/bookings/manager-quick", json={
                "customer_name": "Test Person", "customer_phone": "9876500011",
                "lines": [{"vehicle_type": str(ObjectId()), "service_ids": [str(ObjectId())]}],
                "scheduled_date": "2026-10-10", "scheduled_slot": "09:00-12:00", "payment_method": "subscription",
                "address": {"line1": "1 Test Road", "pincode": "452001"},
            }, headers=team["h"])
            assert plan_paid.status_code == 422
            assert "cash or online" in plan_paid.text
    finally:
        await db.users.delete_one({"_id": ObjectId(customer)})
