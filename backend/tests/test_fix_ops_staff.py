"""
OPS remediation (audit 2026-10-07) — staff changes must never strand live work.

CAP-01 / CAP-05: a captain moved to another center, demoted, suspended or
set inactive kept his assigned jobs (and could still work them, or left them
with a locked-out captain). Every path that takes a captain off his
center's roster now refuses while he holds live jobs, naming the bookings
so the manager reassigns first.

CAP-07: approving leave ignored the jobs already assigned on those days.

Inactive status filters: "inactive" switches an account off
(authz.account_switched_off), so staff pickers / alert fan-outs skip it
exactly like "suspended".
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.utils.timezone import now_ist
from tests.factories import make_captain, make_manager, make_service_center

pytestmark = pytest.mark.asyncio


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_seq = iter(range(1, 10_000))


async def _job(db, cleanup, captain_id: str, center_id: str, status: str = "assigned", *, days: int = 1) -> str:
    """A booking held by this captain — inserted directly, the guard only
    reads captain_id / status / booking_number."""
    day = (now_ist() + timedelta(days=days)).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    number = f"BKOPS{next(_seq):05d}{ObjectId()}"[:20]
    res = await db.bookings.insert_one({
        "booking_number": number, "captain_id": captain_id, "service_center_id": center_id, "status": status,
        # Unique per row: the active-slot unique index keys on these.
        "customer_id": f"ops-{number}", "visit_line_key": number,
        "scheduled_date": day, "scheduled_slot": "09:00-12:00", "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
    })
    cleanup.append(("bookings", {"_id": res.inserted_id}))
    return number


@pytest.fixture
async def rig(db, cleanup):
    ca = await make_service_center(db)
    cb = await make_service_center(db)
    mgr_a = await make_manager(db, ca)
    mgr_b = await make_manager(db, cb)
    cap = await make_captain(db, ca)
    admin = await db.users.find_one({"role": "admin"})
    now = datetime.now(timezone.utc)
    await db.users.update_many({"_id": {"$in": [ObjectId(cap)]}}, {"$set": {"created_at": now}})
    ids = [mgr_a, mgr_b, cap]
    cleanup.append(("captain_wallets", {"captain_id": cap}))
    cleanup.append(("leave_requests", {"captain_id": cap}))
    cleanup.append(("audit_logs", {"target_id": {"$in": ids}}))
    cleanup.append(("users", {"_id": {"$in": [ObjectId(i) for i in ids]}}))
    cleanup.append(("service_centers", {"_id": {"$in": [ObjectId(ca), ObjectId(cb)]}}))
    return {
        "ca": ca, "cb": cb, "mgr_a": mgr_a, "mgr_b": mgr_b, "cap": cap,
        "ADM": _auth(str(admin["_id"]), "admin"), "MA": _auth(mgr_a, "manager", ca), "MB": _auth(mgr_b, "manager", cb),
    }


async def _user(db, uid):
    return await db.users.find_one({"_id": ObjectId(uid)})


# ---------------------------------------------------------------- CAP-01 / CAP-05


async def test_admin_cannot_move_a_captain_holding_live_jobs(db, cleanup, rig):
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "assigned")
    async with _client() as c:
        res = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"service_center_id": rig["cb"]})
    assert res.status_code == 400, res.text
    assert number in res.json()["message"] and "reassign" in res.json()["message"].lower()
    assert (await _user(db, rig["cap"]))["service_center_id"] == rig["ca"]


async def test_admin_cannot_demote_a_captain_holding_live_jobs(db, cleanup, rig):
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "captain_on_the_way")
    async with _client() as c:
        res = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"role": "customer"})
    assert res.status_code == 400, res.text
    assert number in res.json()["message"]
    assert (await _user(db, rig["cap"]))["role"] == "captain"


@pytest.mark.parametrize("status", ["suspended", "inactive"])
async def test_admin_cannot_switch_off_a_captain_holding_live_jobs(db, cleanup, rig, status):
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "assigned")
    async with _client() as c:
        res = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"status": status})
    assert res.status_code == 400, res.text
    assert number in res.json()["message"]
    doc = await _user(db, rig["cap"])
    assert doc["status"] == "active" and doc.get("token_version", 0) == 0


async def test_admin_suspend_route_refuses_mid_job(db, cleanup, rig):
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "service_started")
    async with _client() as c:
        res = await c.post(f"/api/v1/users/{rig['cap']}/suspend", headers=rig["ADM"])
    assert res.status_code == 400, res.text
    assert number in res.json()["message"]
    assert (await _user(db, rig["cap"]))["status"] == "active"


async def test_manager_suspend_refuses_while_a_job_is_merely_assigned(db, cleanup, rig):
    """Assigned (not yet started) used to slip through — the job then sat on
    a captain who could no longer sign in."""
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "assigned")
    async with _client() as c:
        res = await c.post(f"/api/v1/staff/captains/{rig['cap']}/status", headers=rig["MA"], json={"status": "suspended"})
        # Cross-center: manager B can't touch center A's captain at all.
        foreign = await c.post(f"/api/v1/staff/captains/{rig['cap']}/status", headers=rig["MB"], json={"status": "suspended"})
    assert res.status_code == 400, res.text
    assert number in res.json()["message"]
    assert foreign.status_code == 403
    assert (await _user(db, rig["cap"]))["status"] == "active"


async def test_society_captain_cannot_be_moved_until_replaced(db, cleanup, rig):
    soc = await db.societies.insert_one({
        "name": "Green Park OPS", "service_center_id": rig["ca"], "daily_captain_id": rig["cap"], "is_deleted": False,
    })
    cleanup.append(("societies", {"_id": soc.inserted_id}))
    async with _client() as c:
        res = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"service_center_id": rig["cb"]})
    assert res.status_code == 400, res.text
    assert "Green Park OPS" in res.json()["message"]
    assert (await _user(db, rig["cap"]))["service_center_id"] == rig["ca"]


async def test_changes_go_through_once_the_captain_holds_no_live_jobs(db, cleanup, rig):
    # Finished / cancelled / unassigned-pending history never blocks anything.
    await _job(db, cleanup, rig["cap"], rig["ca"], "completed")
    await _job(db, cleanup, rig["cap"], rig["ca"], "cancelled")
    async with _client() as c:
        moved = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"service_center_id": rig["cb"]})
        assert moved.status_code == 200, moved.text
        assert (await _user(db, rig["cap"]))["service_center_id"] == rig["cb"]
        # Renaming a captain who IS on a job is not a roster change.
        await _job(db, cleanup, rig["cap"], rig["cb"], "assigned")
        renamed = await c.put(f"/api/v1/users/{rig['cap']}", headers=rig["ADM"], json={"full_name": "Renamed Captain"})
        assert renamed.status_code == 200, renamed.text
        await db.bookings.update_many({"captain_id": rig["cap"], "status": "assigned"}, {"$set": {"status": "completed"}})
        off = await c.post(f"/api/v1/staff/captains/{rig['cap']}/status", headers=rig["MB"], json={"status": "suspended"})
        assert off.status_code == 200, off.text
    assert (await _user(db, rig["cap"]))["status"] == "suspended"


async def test_guard_is_one_shared_helper(db, cleanup, rig):
    """The service-level helper every path calls: refuses a captain with
    live work, ignores non-captains and harmless edits."""
    from app.core.exceptions import BadRequestException
    from app.services.user_service import UserService

    svc = UserService(db)
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "assigned")
    cap = await _user(db, rig["cap"])
    with pytest.raises(BadRequestException, match=number):
        await svc.ensure_captain_can_leave_roster(cap, {"status": "inactive"})
    await svc.ensure_captain_can_leave_roster(cap, {"full_name": "x"})  # not a roster change
    await svc.ensure_captain_can_leave_roster(cap, {"status": "active"})
    mgr = await _user(db, rig["mgr_a"])
    await svc.ensure_captain_can_leave_roster(mgr, {"service_center_id": rig["cb"]})  # managers hold no jobs


# ---------------------------------------------------------------- CAP-07


async def test_leave_approval_refused_while_jobs_are_assigned_on_those_days(db, cleanup, rig):
    number = await _job(db, cleanup, rig["cap"], rig["ca"], "assigned", days=3)
    await _job(db, cleanup, rig["cap"], rig["ca"], "assigned", days=6)  # outside the leave
    day = (now_ist() + timedelta(days=3)).date().isoformat()
    leave = await db.leave_requests.insert_one({
        "captain_id": rig["cap"], "start_date": day, "end_date": day, "reason": "family", "status": "pending", "is_deleted": False,
    })
    lid = str(leave.inserted_id)
    async with _client() as c:
        res = await c.put(f"/api/v1/leave-requests/{lid}/review", headers=rig["MA"], json={"status": "approved"})
        assert res.status_code == 400, res.text
        assert number in res.json()["message"]
        assert (await db.leave_requests.find_one({"_id": leave.inserted_id}))["status"] == "pending"
        # Rejecting is always allowed.
        rej = await c.put(f"/api/v1/leave-requests/{lid}/review", headers=rig["MA"], json={"status": "rejected"})
        assert rej.status_code == 200, rej.text


async def test_leave_approval_goes_through_when_those_days_are_free(db, cleanup, rig):
    await _job(db, cleanup, rig["cap"], rig["ca"], "assigned", days=6)
    await _job(db, cleanup, rig["cap"], rig["ca"], "completed", days=3)
    day = (now_ist() + timedelta(days=3)).date().isoformat()
    leave = await db.leave_requests.insert_one({
        "captain_id": rig["cap"], "start_date": day, "end_date": day, "reason": "family", "status": "pending", "is_deleted": False,
    })
    async with _client() as c:
        res = await c.put(f"/api/v1/leave-requests/{leave.inserted_id}/review", headers=rig["MA"], json={"status": "approved"})
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------- inactive status filters


async def test_inactive_staff_are_skipped_like_suspended_ones(db, cleanup, rig):
    from app.services.complaint_service import ComplaintService
    from app.services.society_service import SocietyService
    from app.services.society_support_service import _center_managers

    await db.users.update_one({"_id": ObjectId(rig["mgr_a"])}, {"$set": {"status": "inactive"}})
    await db.service_centers.update_one({"_id": ObjectId(rig["ca"])}, {"$set": {"manager_id": rig["mgr_a"]}})
    handlers = await ComplaintService(db)._ticket_handlers({"service_center_id": rig["ca"]})
    assert rig["mgr_a"] not in handlers
    assert rig["mgr_a"] not in await _center_managers(db, rig["ca"])

    await db.users.update_one({"_id": ObjectId(rig["cap"])}, {"$set": {"status": "inactive"}})
    picker = await SocietyService(db).captains_for_center(rig["ca"])
    assert rig["cap"] not in [r["id"] for r in picker]
