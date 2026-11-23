"""Admin sidebar red dots — GET /admin/nav-badges counts NEW (unseen) contact
messages, custom-plan requests and coverage requests; opening/marking a list
sets `seen_at`; a new public submission bumps the count and pings the admin
feed; everything is admin-only."""
import random

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.services import report_cache
from app.services.admin_inbox_service import AdminInboxService, backfill_inbox_seen

pytestmark = pytest.mark.asyncio
BADGES = "/api/v1/admin/nav-badges"
SECTIONS = ("contact_messages", "plan_enquiries", "coverage_requests")


def _auth(user_id: str, role: str, tv: int = 0) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': None, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60)


@pytest.fixture
async def admin(db):
    a = await db.users.find_one({"role": "admin"})
    return _auth(str(a["_id"]), "admin", a.get("token_version", 0))


@pytest.fixture(autouse=True)
def fresh_cache():
    report_cache.invalidate("admin_nav")
    yield
    report_cache.invalidate("admin_nav")


@pytest.fixture
def pings(monkeypatch):
    from app.core.ws_manager import manager

    seen: list[tuple] = []

    async def fake(kind, **kw):
        seen.append((kind, kw))

    monkeypatch.setattr(manager, "notify_changed", fake)
    return seen


def _phone() -> str:
    return f"9{random.randint(10**8, 10**9 - 1)}"


async def _badges(c, admin) -> dict:
    res = await c.get(BADGES, headers=admin)
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def _submit_contact(c, db, cleanup) -> str:
    phone = _phone()
    res = await c.post("/api/v1/contact", json={"name": "Dot Test", "phone": phone, "email": "dot@example.com", "message": "Hello there"})
    assert res.status_code == 200, res.text
    cleanup.append(("contact_messages", {"phone": phone}))
    return phone


async def _submit_enquiry(c, cleanup, phone=None) -> str:
    phone = phone or _phone()
    res = await c.post("/api/v1/subscriptions/enquiries", json={
        "name": "Fleet Owner", "phone": phone, "vehicle_count": 4, "services_wanted": "Exterior wash"})
    assert res.status_code == 200, res.text
    cleanup.append(("plan_enquiries", {"phone": phone}))
    return phone


async def _submit_lead(c, cleanup, phone=None, pincode="999001") -> str:
    phone = phone or _phone()
    res = await c.post("/api/v1/coverage-leads", json={"name": "Far Away", "phone": phone, "pincode": pincode})
    assert res.status_code == 200, res.text
    cleanup.append(("coverage_leads", {"phone": phone}))
    return phone


async def test_badges_are_admin_only(db, admin):
    cust = await db.users.find_one({"role": "customer"})
    async with _client() as c:
        assert (await c.get(BADGES)).status_code == 401
        if cust:
            h = _auth(str(cust["_id"]), "customer")
            assert (await c.get(BADGES, headers=h)).status_code == 403
            assert (await c.post("/api/v1/admin/inbox/contact_messages/seen", headers=h)).status_code == 403
        assert (await c.post("/api/v1/admin/inbox/contact_messages/seen")).status_code == 401
        data = await _badges(c, admin)
    assert set(data) == set(SECTIONS)
    assert all(isinstance(v, int) and v >= 0 for v in data.values())


async def test_new_submissions_bump_counts_and_ping_admin(db, cleanup, admin, pings):
    async with _client() as c:
        before = await _badges(c, admin)
        await _submit_contact(c, db, cleanup)
        await _submit_enquiry(c, cleanup)
        await _submit_lead(c, cleanup)
        after = await _badges(c, admin)
    for s in SECTIONS:
        assert after[s] == before[s] + 1, s
    # One "inbox" ping per creation, each carrying its section (data never travels).
    assert [(k, kw["section"]) for k, kw in pings if k == "inbox"] == [
        ("inbox", "contact_messages"), ("inbox", "plan_enquiries"), ("inbox", "coverage_requests")]
    assert all(kw.get("admin", True) and not kw.get("users") for _k, kw in pings)


async def test_mark_seen_by_ids_then_all(db, cleanup, admin, pings):
    async with _client() as c:
        await AdminInboxService(db).mark_seen("contact_messages", None, "setup")
        base = await _badges(c, admin)
        assert base["contact_messages"] == 0
        p1, p2, p3 = [await _submit_contact(c, db, cleanup) for _ in range(3)]
        assert (await _badges(c, admin))["contact_messages"] == 3
        first = await db.contact_messages.find_one({"phone": p1})
        assert first["seen_at"] is None
        res = await c.post("/api/v1/admin/inbox/contact_messages/seen", json={"ids": [str(first["_id"])]}, headers=admin)
        assert res.status_code == 200, res.text
        assert res.json()["data"]["marked"] == 1
        assert res.json()["data"]["counts"]["contact_messages"] == 2
        assert (await db.contact_messages.find_one({"phone": p1}))["seen_at"] is not None
        assert (await _badges(c, admin))["contact_messages"] == 2
        # Same id again: idempotent.
        again = await c.post("/api/v1/admin/inbox/contact_messages/seen", json={"ids": [str(first["_id"])]}, headers=admin)
        assert again.json()["data"]["marked"] == 0
        # No body = mark all as read.
        res = await c.post("/api/v1/admin/inbox/contact_messages/seen", headers=admin)
        assert res.json()["data"]["marked"] == 2
        assert (await _badges(c, admin))["contact_messages"] == 0
        # The list shows seen_at so the page can bold unseen rows.
        listed = await c.get("/api/v1/contact?page_size=5", headers=admin)
        assert all(row["seen_at"] for row in listed.json()["data"] if row["phone"] in (p1, p2, p3))


async def test_unknown_section_refused(admin):
    async with _client() as c:
        res = await c.post("/api/v1/admin/inbox/bogus/seen", headers=admin)
    assert res.status_code == 400


async def test_resubmission_is_new_again_and_triage_marks_seen(db, cleanup, admin, pings):
    async with _client() as c:
        await AdminInboxService(db).mark_seen("plan_enquiries", None, "setup")
        await AdminInboxService(db).mark_seen("coverage_requests", None, "setup")
        phone = await _submit_enquiry(c, cleanup)
        lead_phone = await _submit_lead(c, cleanup)
        assert (await _badges(c, admin))["plan_enquiries"] == 1
        await AdminInboxService(db).mark_seen("plan_enquiries", None, "setup")
        await AdminInboxService(db).mark_seen("coverage_requests", None, "setup")
        assert (await _badges(c, admin))["plan_enquiries"] == 0
        # The same person asks again: one row, "asked 2 times", new again.
        await _submit_enquiry(c, cleanup, phone)
        await _submit_lead(c, cleanup, lead_phone)
        data = await _badges(c, admin)
        assert data["plan_enquiries"] == 1 and data["coverage_requests"] == 1
        assert (await db.plan_enquiries.find_one({"phone": phone}))["requests_count"] == 2
        # Setting a status (triage) also clears the mark.
        row = await db.plan_enquiries.find_one({"phone": phone})
        res = await c.post(f"/api/v1/subscriptions/enquiries/{row['_id']}/status", params={"status": "contacted"}, headers=admin)
        assert res.status_code == 200, res.text
        assert (await _badges(c, admin))["plan_enquiries"] == 0


async def test_deleted_rows_do_not_count(db, cleanup, admin):
    phone = _phone()
    r = await db.contact_messages.insert_one({"name": "x", "phone": phone, "email": "x@example.com", "message": "m", "seen_at": None, "is_deleted": True})
    cleanup.append(("contact_messages", {"_id": r.inserted_id}))
    before = (await AdminInboxService(db).counts())["contact_messages"]
    r2 = await db.contact_messages.insert_one({"name": "x", "phone": phone, "email": "x@example.com", "message": "m", "seen_at": None, "is_deleted": False})
    cleanup.append(("contact_messages", {"_id": r2.inserted_id}))
    assert (await AdminInboxService(db).counts())["contact_messages"] == before + 1


async def test_backfill_marks_old_rows_seen_but_keeps_new_enquiries(db, cleanup):
    old = await db.contact_messages.insert_one({"name": "old", "phone": _phone(), "email": "o@example.com", "message": "m"})
    e_new = await db.plan_enquiries.insert_one({"name": "n", "phone": _phone(), "status": "new"})
    e_old = await db.plan_enquiries.insert_one({"name": "o", "phone": _phone(), "status": "contacted"})
    cleanup.append(("contact_messages", {"_id": old.inserted_id}))
    cleanup.append(("plan_enquiries", {"_id": {"$in": [e_new.inserted_id, e_old.inserted_id]}}))
    await backfill_inbox_seen(db)
    assert (await db.contact_messages.find_one({"_id": old.inserted_id}))["seen_at"] is not None
    assert (await db.plan_enquiries.find_one({"_id": e_old.inserted_id}))["seen_at"] is not None
    assert (await db.plan_enquiries.find_one({"_id": e_new.inserted_id}))["seen_at"] is None
    await backfill_inbox_seen(db)  # idempotent: the new enquiry stays new
    assert (await db.plan_enquiries.find_one({"_id": e_new.inserted_id}))["seen_at"] is None
