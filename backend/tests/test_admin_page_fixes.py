"""
Admin panel click-through fixes (2026-10 admin pass) — each test pins one
defect found walking every admin screen:

- coupon edit: changing the discount TYPE used to be silently dropped
  (flat ₹100 saved as 100% off); the edited coupon is validated as a whole;
- subscription plan delete is refused while customers still hold it;
- a cancelled capacity change no longer blocks re-scheduling that date (500);
- re-using a taken name surfaces as 409, never a 500;
- optional service / center / combo fields can be cleared (null);
- password change keeps the CURRENT session (fresh token pair) while every
  other device is signed out;
- admin reviews filter on the server.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.utils.timezone import now_ist
from tests.factories import get_hatchback_type_id, make_manager, make_service_center, make_subscription_plan


def _auth(user_id: str, role: str, center_id: str | None = None, tv: int = 0) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def admin_headers(db):
    admin = await db.users.find_one({"role": "admin"})
    return _auth(str(admin["_id"]), "admin", admin.get("service_center_id"), admin.get("token_version", 0))


@pytest.mark.asyncio
async def test_coupon_type_change_is_saved_and_validated(db, cleanup, admin_headers):
    code = f"ADMFIX{ObjectId()}"[-10:].upper()
    cleanup.append(("coupons", {"code": code}))
    now = datetime.now(timezone.utc)
    async with _client() as client:
        res = await client.post("/api/v1/coupons", json={
            "code": code, "coupon_type": "percentage", "value": 20, "valid_from": now.isoformat(),
            "valid_until": (now + timedelta(days=5)).isoformat(),
        }, headers=admin_headers)
        assert res.status_code == 200, res.text
        cid = res.json()["data"]["id"]
        res = await client.put(f"/api/v1/coupons/{cid}", json={"coupon_type": "flat", "value": 100}, headers=admin_headers)
        assert res.status_code == 200, res.text
        assert (res.json()["data"]["coupon_type"], res.json()["data"]["value"]) == ("flat", 100)
        # Switching back to percentage with a flat-sized value is refused.
        await client.put(f"/api/v1/coupons/{cid}", json={"value": 150}, headers=admin_headers)
        res = await client.put(f"/api/v1/coupons/{cid}", json={"coupon_type": "percentage"}, headers=admin_headers)
        assert res.status_code == 400  # 150% off
        # A description can be cleared.
        await client.put(f"/api/v1/coupons/{cid}", json={"description": "Launch"}, headers=admin_headers)
        res = await client.put(f"/api/v1/coupons/{cid}", json={"description": None}, headers=admin_headers)
        assert res.json()["data"].get("description") is None


@pytest.mark.asyncio
async def test_plan_with_subscribers_cannot_be_deleted(db, cleanup, admin_headers):
    plan_id = await make_subscription_plan(db, vehicle_types=[await get_hatchback_type_id(db)])
    sub = await db.user_subscriptions.insert_one({
        "customer_id": str(ObjectId()), "plan_id": plan_id, "status": "active", "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
    })
    cleanup.append(("user_subscriptions", {"_id": sub.inserted_id}))
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    async with _client() as client:
        res = await client.delete(f"/api/v1/subscription-plans/{plan_id}", headers=admin_headers)
        assert res.status_code == 409
        assert (await db.subscription_plans.find_one({"_id": ObjectId(plan_id)})).get("is_deleted") is not True
        await db.user_subscriptions.update_one({"_id": sub.inserted_id}, {"$set": {"status": "cancelled"}})
        res = await client.delete(f"/api/v1/subscription-plans/{plan_id}", headers=admin_headers)
        assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_rescheduling_a_cancelled_capacity_date_works(db, cleanup, admin_headers):
    center = await make_service_center(db)
    cleanup.append(("capacity_policy_changes", {"service_center_id": center}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    future = (now_ist() + timedelta(days=10)).strftime("%Y-%m-%d")
    async with _client() as client:
        res = await client.post(f"/api/v1/service-centers/{center}/capacity-policy", json={"effective_date": future, "max_bookings_per_day": 12}, headers=admin_headers)
        assert res.status_code == 200, res.text
        change_id = res.json()["data"]["id"]
        res = await client.delete(f"/api/v1/service-centers/{center}/capacity-policy/{change_id}", headers=admin_headers)
        assert res.status_code == 200, res.text
        res = await client.post(f"/api/v1/service-centers/{center}/capacity-policy", json={"effective_date": future, "max_bookings_per_day": 9}, headers=admin_headers)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["max_bookings_per_day"] == 9


@pytest.mark.asyncio
async def test_duplicate_names_are_409_and_optional_fields_clear(db, cleanup, admin_headers):
    category = await db.categories.find_one({})
    name = f"Admin Fix Wash {ObjectId()}"
    hatch = await get_hatchback_type_id(db)
    cleanup.append(("services", {"name": name}))
    async with _client() as client:
        body = {"category_id": str(category["_id"]), "name": name, "price": 300, "duration_minutes": 30, "vehicle_types": [hatch], "discounted_price": 199, "variant_group": "g1"}
        res = await client.post("/api/v1/services", json=body, headers=admin_headers)
        assert res.status_code == 200, res.text
        sid = res.json()["data"]["id"]
        dup = await client.post("/api/v1/services", json=body, headers=admin_headers)
        assert dup.status_code == 409, dup.text
        res = await client.put(f"/api/v1/services/{sid}", json={"discounted_price": None, "variant_group": None}, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["data"].get("discounted_price") is None and not res.json()["data"].get("variant_group")
        assert (await client.put(f"/api/v1/services/{sid}", json={"price": 0}, headers=admin_headers)).status_code == 422

    center = await make_service_center(db)
    manager = await make_manager(db, center)
    cleanup.append(("users", {"_id": ObjectId(manager)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    async with _client() as client:
        res = await client.put(f"/api/v1/service-centers/{center}", json={"manager_id": manager, "contact_phone": "9876500000"}, headers=admin_headers)
        assert res.status_code == 200 and res.json()["data"]["manager_id"] == manager
        res = await client.put(f"/api/v1/service-centers/{center}", json={"manager_id": None, "contact_phone": None}, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["data"].get("manager_id") is None and res.json()["data"].get("contact_phone") is None
        bad = await client.put(f"/api/v1/service-centers/{center}", json={"manager_id": str(ObjectId())}, headers=admin_headers)
        assert bad.status_code == 400


@pytest.mark.asyncio
async def test_password_change_keeps_this_session_and_ends_the_others(db, cleanup):
    from app.core.security import hash_password
    from app.services.auth_service import AuthService

    center = await make_service_center(db)
    manager_id = await make_manager(db, center)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    await db.users.update_one({"_id": ObjectId(manager_id)}, {"$set": {"password_hash": hash_password("Old@12345")}})
    user = await db.users.find_one({"_id": ObjectId(manager_id)})
    other_device = AuthService(db)._issue_tokens(user)
    async with _client() as client:
        res = await client.post(
            "/api/v1/auth/change-password", json={"current_password": "Old@12345", "new_password": "New@12345"},
            headers={"Authorization": f"Bearer {other_device['access_token']}"},
        )
        assert res.status_code == 200, res.text
        fresh = res.json()["data"]
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh['access_token']}"})
        assert me.status_code == 200
        stale = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {other_device['access_token']}"})
        assert stale.status_code == 401


@pytest.mark.asyncio
async def test_admin_reviews_filter_on_the_server(db, cleanup, admin_headers):
    center = str(ObjectId())
    docs = [
        {"booking_id": str(ObjectId()), "customer_id": str(ObjectId()), "service_center_id": center, "captain_rating": r, "service_rating": r,
         "created_at": datetime.now(timezone.utc), "is_deleted": False}
        for r in (5, 4, 2)
    ]
    ids = (await db.reviews.insert_many(docs)).inserted_ids
    cleanup.append(("reviews", {"_id": {"$in": ids}}))
    async with _client() as client:
        res = await client.get(f"/api/v1/reviews/admin/all?service_center_id={center}&min_rating=4", headers=admin_headers)
        assert res.status_code == 200, res.text
        assert res.json()["meta"]["total"] == 2
        res = await client.get(f"/api/v1/reviews/admin/all?service_center_id={center}&page_size=1&page=3", headers=admin_headers)
        assert res.json()["meta"]["total"] == 3 and len(res.json()["data"]) == 1


@pytest.mark.asyncio
async def test_inbox_template_picker_only_offers_sendable_templates(db, cleanup, admin_headers):
    names = [f"admfix_{k}_{ObjectId()}" for k in ("plain", "urlbtn", "otp", "off")]
    await db.whatsapp_templates.insert_many([
        {"name": names[0], "status": "APPROVED", "category": "UTILITY", "body": "Hi {{1}}", "param_count": 1},
        {"name": names[1], "status": "APPROVED", "category": "UTILITY", "body": "Track", "has_url_param": True},
        {"name": names[2], "status": "APPROVED", "category": "AUTHENTICATION", "body": "{{1}} is your code"},
        {"name": names[3], "status": "APPROVED", "category": "UTILITY", "body": "x", "disabled": True},
    ])
    cleanup.append(("whatsapp_templates", {"name": {"$in": names}}))
    async with _client() as client:
        res = await client.get("/api/v1/whatsapp/crm/templates?sendable=true", headers=admin_headers)
        assert res.status_code == 200, res.text
        offered = {t["name"] for t in res.json()["data"]} & set(names)
        assert offered == {names[0]}
        everything = await client.get("/api/v1/whatsapp/crm/templates", headers=admin_headers)
        assert set(names) <= {t["name"] for t in everything.json()["data"]}
