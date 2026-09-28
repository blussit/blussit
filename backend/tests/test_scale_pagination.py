"""
Scale pass: list endpoints that used to return (or filter client-side over)
everything now page and search on the server — complaints (admin + center),
the CRM customer typeahead — and the report cache in front of admin
dashboards shares one computation per key and expires.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services import report_cache
from app.services.complaint_service import ComplaintService
from app.services.crm_service import CRMService

MARK = {"_scale_pagination": True}


@pytest.fixture
async def complaints_rig(db):
    await db.complaints.delete_many(MARK)
    await db.users.delete_many(MARK)
    await db.bookings.delete_many(MARK)
    center_a, center_b = str(ObjectId()), str(ObjectId())
    people = []
    for i, name in enumerate(["Asha Verma", "Bilal Khan", "Chitra Rao"]):
        res = await db.users.insert_one({**MARK, "role": "customer", "full_name": name, "phone": f"98100000{i:02d}", "is_deleted": False,
                                         "created_at": datetime.now(timezone.utc)})
        people.append(str(res.inserted_id))
    booking = await db.bookings.insert_one({**MARK, "booking_number": "BKSCALEPAG1", "customer_id": people[1], "service_center_id": center_a})
    base = datetime.now(timezone.utc) - timedelta(days=1)
    docs = []
    for i in range(25):
        docs.append({
            **MARK, "subject": f"Water spots #{i}" if i % 5 else f"Late arrival #{i}",
            "customer_id": people[i % 3], "service_center_id": center_a if i % 2 else center_b,
            "booking_id": str(booking.inserted_id) if i == 7 else str(ObjectId()),
            "status": "open" if i % 4 else "resolved", "priority": "medium", "replies": [],
            "created_at": base + timedelta(minutes=i), "is_deleted": False,
        })
    await db.complaints.insert_many(docs)
    yield {"people": people, "center_a": center_a, "center_b": center_b}
    await db.complaints.delete_many(MARK)
    await db.users.delete_many(MARK)
    await db.bookings.delete_many(MARK)


@pytest.mark.asyncio
async def test_admin_complaints_search_center_filter_and_paging(db, complaints_rig):
    svc = ComplaintService(db)
    scoped = {"_scale_pagination": True}
    page1, total = await svc.list_all(scoped, 1, 10)
    page3, _ = await svc.list_all(scoped, 3, 10)
    assert total == 25 and len(page1) == 10 and len(page3) == 5
    assert page1[0]["subject"].endswith("#24")  # newest first

    by_subject, n = await svc.list_all(scoped, 1, 50, search="late arrival")
    assert n == 5 and all("Late arrival" in c["subject"] for c in by_subject)

    by_name, n = await svc.list_all(scoped, 1, 50, search="bilal")
    assert n == len([i for i in range(25) if i % 3 == 1]) and all(c["customer_name"] == "Bilal Khan" for c in by_name)

    by_phone, n = await svc.list_all(scoped, 1, 50, search="+91 98100 00002")
    assert n and all(c["customer_name"] == "Chitra Rao" for c in by_phone)

    by_booking, n = await svc.list_all(scoped, 1, 50, search="bkscalepag1")
    assert n == 1 and by_booking[0]["booking_number"] == "BKSCALEPAG1"

    center, n = await svc.list_all({**scoped, "service_center_id": complaints_rig["center_a"]}, 1, 50)
    assert n == 12 and all(c["service_center_id"] == complaints_rig["center_a"] for c in center)


@pytest.mark.asyncio
async def test_center_complaints_search_stays_inside_the_center(db, complaints_rig):
    svc = ComplaintService(db)
    rows, n = await svc.list_for_center(complaints_rig["center_b"], "open", 1, 50, "manager", complaints_rig["center_b"], search="water")
    assert n and all(c["service_center_id"] == complaints_rig["center_b"] and c["status"] == "open" and "Water" in c["subject"] for c in rows)


@pytest.mark.asyncio
async def test_customer_typeahead_matches_name_or_pasted_phone_without_staff(db, complaints_rig):
    crm = CRMService(db)
    assert [u["full_name"] for u in await crm.search_customers("asha")] == ["Asha Verma"]
    assert [u["full_name"] for u in await crm.search_customers("+91 98100 00001")] == ["Bilal Khan"]
    assert await crm.search_customers("a") == []  # too short
    assert all(u["role"] == "customer" for u in await crm.search_customers("admin", limit=25))


@pytest.mark.asyncio
async def test_report_cache_single_flight_ttl_and_invalidate():
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"n": calls}

    key = ("scale_test", "k1")
    report_cache.invalidate("scale_test")
    first, second = await asyncio.gather(report_cache.cached(key, 60, compute), report_cache.cached(key, 60, compute))
    assert calls == 1 and first == second == {"n": 1}
    assert await report_cache.cached(key, 60, compute) == {"n": 1}  # served from cache
    report_cache.invalidate("scale_test")
    assert await report_cache.cached(key, 60, compute) == {"n": 2}
    short = ("scale_test", "short")
    await report_cache.cached(short, 0.01, compute)
    await asyncio.sleep(0.02)
    assert (await report_cache.cached(short, 0.01, compute))["n"] == calls


@pytest.mark.asyncio
async def test_report_cache_does_not_keep_failures():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "ok"

    report_cache.invalidate("scale_test_fail")
    with pytest.raises(RuntimeError):
        await report_cache.cached(("scale_test_fail",), 60, flaky)
    assert await report_cache.cached(("scale_test_fail",), 60, flaky) == "ok"


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


@pytest.mark.asyncio
async def test_paged_report_routes_are_wired_end_to_end(db, complaints_rig):
    """Query params reach the services through the real routes + auth."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    admin = await db.users.find_one({"role": "admin"})
    headers = _auth(str(admin["_id"]), "admin")
    center = complaints_rig["center_a"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/complaints", params={"service_center_id": center, "search": "late", "page_size": 5}, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["meta"]["page_size"] == 5 and all(c["service_center_id"] == center and "Late" in c["subject"] for c in body["data"])

        res = await client.get("/api/v1/subscriptions/admin/overview", params={"status": "active", "page": 1, "page_size": 3, "search": "asha"}, headers=headers)
        assert res.status_code == 200, res.text
        overview = res.json()["data"]
        assert set(overview) >= {"kpis", "plan_breakdown", "rows", "meta", "plans"} and overview["meta"]["page_size"] == 3

        res = await client.get(f"/api/v1/subscriptions/center/{center}/overview", params={"status": "expiring", "page_size": 2}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["meta"]["page_size"] == 2

        res = await client.get("/api/v1/crm/customers/typeahead", params={"q": "chitra", "limit": 3}, headers=headers)
        assert res.status_code == 200 and [u["full_name"] for u in res.json()["data"]] == ["Chitra Rao"]

        res = await client.get("/api/v1/whatsapp/crm/conversations", params={"limit": 2}, headers=headers)
        assert res.status_code == 200 and len(res.json()["data"]) <= 2
        assert (await client.get("/api/v1/whatsapp/crm/conversations", params={"before": "not-a-date"}, headers=headers)).status_code == 400

        badge = await client.get("/api/v1/whatsapp/crm/badge", headers=headers)
        assert badge.status_code == 200 and "unread_conversations" in badge.json()["data"]

        first = await client.get("/api/v1/analytics/kpis/overview", params={"period": "7d"}, headers=headers)
        again = await client.get("/api/v1/analytics/kpis/overview", params={"period": "7d"}, headers=headers)
        assert first.status_code == again.status_code == 200 and first.json()["data"] == again.json()["data"]

        # Customers and managers stay locked out of admin-only reports.
        customer_headers = _auth(complaints_rig["people"][0], "customer")
        assert (await client.get("/api/v1/subscriptions/admin/overview", headers=customer_headers)).status_code == 403
        manager = await db.users.find_one({"role": "manager"})
        manager_headers = _auth(str(manager["_id"]), "manager", manager.get("service_center_id"))
        assert (await client.get("/api/v1/analytics/kpis/customers", headers=manager_headers)).status_code == 403
