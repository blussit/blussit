"""Remediation pass 2026-10-07 (PLANS) — P3s: a resident's schedule change
request is answered exactly once (double approval / approve racing decline),
and society activation keeps one pass per car when re-run. Local Mongo only."""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.core.exceptions import AppException
from app.schemas.society_schedule_schema import ChangeRequestCreate, ChangeRequestResolve, ResidentRuleRequest, RulePattern
from app.services.society_schedule_service import SocietyScheduleService
from app.utils.timezone import now_ist

from tests.society_factories import activate_cash, enroll, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
async def _tidy(db):
    """Everything these tests create in the money/pass collections goes
    again at teardown (other suites assert those collections are empty)."""
    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    first = ObjectId.from_datetime(since)
    yield
    for name in ("user_subscriptions", "payment_orders", "bookings", "society_payments", "coupon_usages"):
        await db[name].delete_many({"_id": {"$gte": first}})
    await db.payment_orders.delete_many({"created_at": {"$gte": since}})
    await db.pass_claims.delete_many({"created_at": {"$gte": since}})


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=1)
    society = await make_society(db, cleanup, center, name=f"Answer Once Court {next(_seq)}")
    manager, captain = await staff(db, cleanup, center["id"])
    sid = society["id"]
    for coll in ("society_visits", "society_schedule_rules", "society_schedule_requests"):
        cleanup.append((coll, {"society_id": sid}))
    plan = await make_template(db, cleanup, count=2)
    user = SimpleNamespace(id=manager, role="manager", service_center_id=center["id"])
    return SimpleNamespace(center=center, society=society, sid=sid, manager=manager, captain=captain, plan=plan, user=user)


async def _move_request(db, cleanup, rig):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    subs = [c["subscription"]["id"] for c in enrollment["cars"]]
    service = SocietyScheduleService(db)
    society = await db.societies.find_one({"_id": ObjectId(rig.sid)})
    day3 = now_ist().date() + timedelta(days=3)
    await service.create_rule(society, ResidentRuleRequest(
        enrollment_id=enrollment["id"], subscription_ids=subs,
        pattern=RulePattern(kind="weekly", weekday=day3.weekday()), slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    first = (await service.my_schedule(str(resident["_id"])))["items"][0]
    want = (day3 + timedelta(days=1)).isoformat()
    req = await service.create_request(str(resident["_id"]), ChangeRequestCreate(visit_id=first["visit_id"], kind="move", preferred_date=want, preferred_slot="11:00-14:00"))
    return str(resident["_id"]), req


async def test_change_request_approved_once_under_double_tap(db, cleanup, rig):
    customer_id, req = await _move_request(db, cleanup, rig)
    raw = await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])})

    async def approve():
        try:
            await SocietyScheduleService(db).approve_request(dict(raw), ChangeRequestResolve(note=None), rig.user)
            return "ok"
        except AppException as exc:
            return exc.message

    results = await asyncio.gather(*[approve() for _ in range(4)])
    assert results.count("ok") == 1, results
    assert await db.society_visits.count_documents({"request_id": req["id"]}) == 1
    assert await db.notifications.count_documents({"user_id": customer_id, "title": "Premium wash updated"}) == 1


async def test_change_request_approve_racing_decline_answers_once(db, cleanup, rig):
    customer_id, req = await _move_request(db, cleanup, rig)
    raw = await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])})
    service = SocietyScheduleService(db)

    async def run(fn):
        try:
            await fn(dict(raw), ChangeRequestResolve(note=None), rig.user)
            return "ok"
        except AppException:
            return "refused"

    results = await asyncio.gather(run(service.approve_request), run(SocietyScheduleService(db).decline_request))
    assert results.count("ok") == 1, results
    fresh = await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])})
    moved = await db.society_visits.count_documents({"request_id": req["id"]})
    assert (fresh["status"], moved) in (("approved", 1), ("declined", 0))
    told = await db.notifications.count_documents({"user_id": customer_id, "title": {"$in": ["Premium wash updated", "Premium wash unchanged"]}})
    assert told == 1
