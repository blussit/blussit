"""
Society premium-wash scheduling (docs/SOCIETY_PLANS.md §9):

  * the pure engine — rotation over weekends, repeat patterns, capacity,
    urgency + spacing, and never more washes than a car has left;
  * repeat rules -> occurrences (idempotent) + single-occurrence changes;
  * the sweep turning a visit day into real bookings through the society
    premium-booking path (quota spent exactly once, captains assigned at
    staggered starts), and re-running it never booking twice;
  * resident change requests.
"""
import itertools
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.schemas.society_schedule_schema import (
    ChangeRequestCreate,
    ChangeRequestResolve,
    ResidentRuleRequest,
    RulePattern,
    SocietyRuleRequest,
    VisitCreateRequest,
    VisitUpdateRequest,
)
from app.services.society_schedule_engine import Car, Visit, lay_out, pattern_dates, project, rotation_patterns, slot_from_key
from app.services.society_schedule_service import SocietyScheduleService
from app.utils.timezone import IST, now_ist

from tests.factories import make_captain
from tests.society_factories import activate_cash, enroll, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)
SLOTS = {k: slot_from_key(k) for k in ("08:00-11:00", "11:00-14:00", "14:00-17:00", "17:00-20:00")}


def _car(i: int, *, remaining: int = 2, total: int = 2, start: date = date(2026, 10, 1), end: date = date(2026, 11, 1), enrollment: str | None = None) -> Car:
    return Car(sub_id=f"s{i}", vehicle_id=f"v{i}", enrollment_id=enrollment or f"e{i}", customer_id=f"c{i}", remaining=remaining,
               cycle_start=start, end=end, total=total, minutes=45, flat=f"A-{i:02d}", plate=f"MP09X{i:04d}")


# ---------------------------------------------------------------------------
# Engine (pure)
# ---------------------------------------------------------------------------


async def test_rotation_covers_four_societies_over_two_weekends_then_repeats():
    start = date(2026, 10, 10)  # a Saturday
    patterns = rotation_patterns(4, [5, 6], start)
    first_round = [pattern_dates(p, start, date(2026, 10, 18)) for p in patterns]
    # Sat wk1 A, Sun wk1 B, Sat wk2 C, Sun wk2 D — every society exactly once.
    assert first_round == [[date(2026, 10, 10)], [date(2026, 10, 11)], [date(2026, 10, 17)], [date(2026, 10, 18)]]
    assert all(p["interval_weeks"] == 2 for p in patterns)
    # Next round, same order.
    second = [pattern_dates(p, date(2026, 10, 19), date(2026, 11, 1)) for p in patterns]
    assert second == [[date(2026, 10, 24)], [date(2026, 10, 25)], [date(2026, 10, 31)], [date(2026, 11, 1)]]
    # Odd count: 3 societies over one weekend day -> every 3 weeks each.
    assert [p["interval_weeks"] for p in rotation_patterns(3, [6], start)] == [3, 3, 3]


async def test_repeat_patterns():
    sat = {"kind": "monthly_nth", "weekday": 5, "weeks": [1, 3]}
    assert pattern_dates(sat, date(2026, 10, 1), date(2026, 10, 31)) == [date(2026, 10, 3), date(2026, 10, 17)]
    weekly = {"kind": "weekly", "weekday": 6}
    assert pattern_dates(weekly, date(2026, 10, 1), date(2026, 10, 31)) == [date(2026, 10, d) for d in (4, 11, 18, 25)]
    # Bounded by the rule's own dates.
    assert pattern_dates(weekly, date(2026, 10, 1), date(2026, 10, 31), rule_start=date(2026, 10, 10), rule_end=date(2026, 10, 20)) == [
        date(2026, 10, 11), date(2026, 10, 18)]


async def test_capacity_is_captains_times_washes_and_starts_are_staggered():
    cars = [_car(i) for i in range(10)]
    day = date(2026, 10, 10)
    one = Visit(id="v1", kind="society", date=day, status="planned", slot_keys=["08:00-11:00", "11:00-14:00"], captain_ids=["capA"], per_captain=6)
    result = project(cars, [one], SLOTS, buffer=15)["v1"]
    assert sum(u["cars"] for u in result["placed"]) == 6
    assert len(result["overflow"]) == 4 and all(u["reason"] == "Visit day is full" for u in result["overflow"])
    # 45-minute washes + 15-minute travel buffer -> one an hour from 8:00.
    assert sorted(u["start"] for u in result["placed"]) == [480 + 60 * k for k in range(6)]
    assert {u["slot_key"] for u in result["placed"]} == {"08:00-11:00", "11:00-14:00"}

    two = Visit(id="v2", kind="society", date=day, status="planned", slot_keys=["08:00-11:00", "11:00-14:00"], captain_ids=["capA", "capB"], per_captain=6)
    result = project(cars, [two], SLOTS, buffer=15)["v2"]
    assert sum(u["cars"] for u in result["placed"]) == 10 and not result["overflow"]
    assert {u["captain_id"] for u in result["placed"]} == {"capA", "capB"}


async def test_one_residents_cars_stay_together_and_inside_one_slot():
    cars = [_car(1, enrollment="e1"), _car(2, enrollment="e1"), _car(3)]
    v = Visit(id="v", kind="society", date=date(2026, 10, 10), status="planned", slot_keys=["08:00-11:00"], captain_ids=["capA"], per_captain=6)
    placed = project(cars, [v], SLOTS, buffer=15)["v"]["placed"]
    pair = next(u for u in placed if u["enrollment_id"] == "e1")
    assert pair["cars"] == 2 and pair["minutes"] == 90
    # Both cars must START inside the booking slot (last car by 11:00).
    assert pair["start"] + pair["last_offset"] <= 11 * 60


async def test_spacing_and_quota_over_weekly_visits():
    # Plan month 1–31 Oct, 2 premium washes, a visit every Saturday.
    cars = [_car(1, remaining=2, total=2)]
    sats = [date(2026, 10, d) for d in (3, 10, 17, 24, 31)]
    visits = [Visit(id=f"w{i}", kind="society", date=d, status="planned", slot_keys=["08:00-11:00"], captain_ids=["capA"], per_captain=6, order=i)
              for i, d in enumerate(sats)]
    out = project(cars, visits, SLOTS, buffer=15)
    washed_on = [v.date for v in visits if out[v.id]["placed"]]
    # Never more than the 2 it has, and spread out (not two weekends running).
    assert len(washed_on) == 2
    assert (washed_on[1] - washed_on[0]).days >= 14
    last = out["w4"]["skipped"][0]["reason"]
    assert "No premium washes left" in last


async def test_the_most_urgent_car_gets_the_last_place():
    # A: 2 left, 1 later chance -> must go now. B: 1 left, plenty of chances.
    a = _car(1, remaining=2, total=2)
    b = _car(2, remaining=1, total=1)
    days = [date(2026, 10, 10), date(2026, 10, 17)]
    visits = [Visit(id="v0", kind="society", date=days[0], status="planned", slot_keys=["08:00-11:00"], captain_ids=["capA"], per_captain=1),
              Visit(id="v1", kind="society", date=days[1], status="planned", slot_keys=["08:00-11:00"], captain_ids=["capA"], per_captain=1, order=1)]
    out = project([b, a], visits, SLOTS, buffer=15)
    assert out["v0"]["placed"][0]["sub_ids"] == ["s1"]
    assert out["v1"]["placed"][0]["sub_ids"] in (["s1"], ["s2"])


async def test_layout_steps_around_a_captains_other_jobs():
    unit = {"cars": 1, "minutes": 45, "pin": None, "last_offset": 0, "sub_ids": ["x"], "flat": ""}
    window = [SLOTS["08:00-11:00"]]
    placed, _ = lay_out([unit], ["capA"], 6, window, list(SLOTS.values()), 15, {"capA": [(480, 570)]})
    # Busy 8:00–9:30 -> first free start is 9:45 (travel buffer).
    assert placed[0]["start"] == 585


# ---------------------------------------------------------------------------
# Service (database)
# ---------------------------------------------------------------------------


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=5)
    society = await make_society(db, cleanup, center, name=f"Sunrise Residency {next(_seq)}")
    manager, captain = await staff(db, cleanup, center["id"])
    captain2 = await make_captain(db, center["id"])
    cleanup.append(("users", {"_id": ObjectId(captain2)}))
    sid = society["id"]
    for coll in ("society_visits", "society_schedule_rules", "society_schedule_requests"):
        cleanup.append((coll, {"society_id": sid}))
    cleanup.append(("slot_capacity", {"service_center_id": center["id"]}))
    cleanup.append(("notifications", {"user_id": captain2}))
    plan = await make_template(db, cleanup, count=2)
    user = SimpleNamespace(id=manager, role="manager", service_center_id=center["id"])
    return SimpleNamespace(center=center, society=society, sid=sid, manager=manager, captain=captain, captain2=captain2, plan=plan, user=user)


async def _resident(db, cleanup, rig, cars: int = 1) -> dict:
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig.society, resident, rig.plan["id"], cars=cars)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    return {"customer_id": str(resident["_id"]), "enrollment": enrollment, "subs": [c["subscription"]["id"] for c in enrollment["cars"]]}


def _in_hours() -> datetime:
    return now_ist().replace(hour=12, minute=0, second=0, microsecond=0)


async def _society(db, sid):
    return await db.societies.find_one({"_id": ObjectId(sid)})


async def test_resident_rule_materializes_idempotently_and_one_day_can_change(db, cleanup, rig):
    res = await _resident(db, cleanup, rig)
    service = SocietyScheduleService(db)
    day3 = now_ist().date() + timedelta(days=3)
    rule = await service.create_rule(await _society(db, rig.sid), ResidentRuleRequest(
        enrollment_id=res["enrollment"]["id"], subscription_ids=res["subs"],
        pattern=RulePattern(kind="weekly", weekday=day3.weekday()), slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    visits = await db.society_visits.find({"rule_id": rule["id"]}).sort("date", 1).to_list(None)
    assert len(visits) == 5 and visits[0]["date"] == day3.isoformat()
    assert all(v["kind"] == "resident" and v["slot_keys"] == ["08:00-11:00"] for v in visits)
    # Materializing again (sweep, planner, another tab) adds nothing.
    raw = await db.society_schedule_rules.find_one({"_id": ObjectId(rule["id"])})
    assert await service.materialize({**raw, "materialized_until": None}) == 0
    assert await db.society_visits.count_documents({"rule_id": rule["id"]}) == 5

    # Only the first two un-skipped days are covered — the plan has 2 washes.
    first, second, third = visits[0], visits[1], visits[2]
    await service.skip_visit(first, rig.user)
    moved_to = (date.fromisoformat(second["date"]) + timedelta(days=1)).isoformat()
    await service.update_visit(second, VisitUpdateRequest(date=moved_to), rig.user)
    await service.update_visit(third, VisitUpdateRequest(captain_ids=[rig.captain2]), rig.user)
    fresh = {str(v["_id"]): v for v in await db.society_visits.find({"rule_id": rule["id"]}).to_list(None)}
    assert fresh[str(first["_id"])]["status"] == "skipped"
    assert fresh[str(second["_id"])]["date"] == moved_to and fresh[str(second["_id"])]["moved_from"] == second["date"]
    assert fresh[str(third["_id"])]["captain_ids"] == [rig.captain2]
    # The moved day keeps its original key -> the old date is never re-made.
    assert await service.materialize({**raw, "materialized_until": None}) == 0

    view = await service.society_schedule(await _society(db, rig.sid), None, None)
    covered = [v for v in view["visits"] if v["kind"] == "resident" and v["allocations"]]
    assert [v["id"] for v in covered] == [str(second["_id"]), str(third["_id"])]
    assert covered[1]["allocations"][0]["captain_id"] == rig.captain2
    later = [v for v in view["visits"] if v["kind"] == "resident" and v["status"] == "planned" and not v["allocations"]]
    reasons = [s["reason"] for v in later for s in v["skipped"]]
    assert later and all("No premium washes left" in r or "Plan month ends" in r for r in reasons)
    assert any("No premium washes left" in r for r in reasons)


async def test_sweep_books_a_visit_day_once_with_captains_assigned(db, cleanup, rig):
    singles = [await _resident(db, cleanup, rig) for _ in range(3)]
    pair = await _resident(db, cleanup, rig, cars=2)
    service = SocietyScheduleService(db)
    tomorrow = (now_ist().date() + timedelta(days=1)).isoformat()
    created = await service.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=tomorrow, slot_keys=["08:00-11:00", "11:00-14:00"], captain_ids=[rig.captain, rig.captain2], washes_per_captain=3,
    ), rig.manager)

    stats = await service.sweep(now=_in_hours())
    assert stats["generated"] == 1
    visit = await db.society_visits.find_one({"_id": ObjectId(created["id"])})
    assert visit["status"] == "booked", visit.get("generation_error")
    bookings = await db.bookings.find({"society_visit_id": created["id"]}).to_list(None)
    assert len(bookings) == 5
    assert all(b["status"] == "assigned" and b["captain_id"] in (rig.captain, rig.captain2) for b in bookings)
    assert all(b["payment_method"] == "subscription" for b in bookings)
    # One premium wash spent per car, through the normal plan path.
    for sub_id in [s for r in singles for s in r["subs"]] + pair["subs"]:
        sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
        assert sub["remaining_service_count"] == 1
    # The two-car resident is one visit, back to back, one captain.
    pair_bookings = [b for b in bookings if b["subscription_id"] in pair["subs"]]
    assert len({b["booking_group_id"] for b in pair_bookings}) == 1 and len({b["captain_id"] for b in pair_bookings}) == 1
    starts = sorted(b["estimated_start_at"] for b in pair_bookings)
    assert (starts[1] - starts[0]).total_seconds() == 45 * 60
    # Manager: one line for the day, not "New booking — needs a captain" x5.
    assert await db.notifications.count_documents({"user_id": rig.manager, "title": "Society visit booked"}) == 1
    assert await db.notifications.count_documents({"user_id": rig.manager, "title": {"$regex": "^New booking"}}) == 0
    # Residents got their normal booking confirmation.
    assert await db.notifications.count_documents({"user_id": singles[0]["customer_id"], "title": {"$regex": "booked$"}}) == 1

    # Re-running the sweep books nothing more.
    await service.sweep(now=_in_hours())
    assert await db.bookings.count_documents({"society_visit_id": created["id"]}) == 5
    # A generator that died after booking (visit back to planned, nothing
    # recorded) adopts the existing bookings instead of booking twice.
    await db.society_visits.update_one({"_id": ObjectId(created["id"])}, {"$set": {"status": "planned", "allocations": []}})
    await service.sweep(now=_in_hours())
    assert await db.bookings.count_documents({"subscription_id": {"$in": [s for r in singles for s in r["subs"]] + pair["subs"]}, "status": {"$ne": "cancelled"}}) == 5
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(singles[0]["subs"][0])})
    assert sub["remaining_service_count"] == 1
    visit = await db.society_visits.find_one({"_id": ObjectId(created["id"])})
    assert visit["status"] == "booked" and sum(len(a["sub_ids"]) for a in visit["allocations"]) == 5


async def test_quota_is_never_exceeded_across_visit_days(db, cleanup, rig):
    res = await _resident(db, cleanup, rig)
    sub_id = res["subs"][0]
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"remaining_service_count": 1}})
    service = SocietyScheduleService(db)
    society = await _society(db, rig.sid)
    today = now_ist().date()
    for offset in (1, 2):
        await service.create_visit(society, VisitCreateRequest(
            date=(today + timedelta(days=offset)).isoformat(), slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
        ), rig.manager)
    await service.sweep(now=_in_hours())
    live = await db.bookings.count_documents({"subscription_id": sub_id, "status": {"$ne": "cancelled"}})
    assert live == 1
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)})
    assert sub["remaining_service_count"] == 0
    statuses = sorted(v["status"] for v in await db.society_visits.find({"society_id": rig.sid}).to_list(None))
    assert statuses == ["booked", "empty"]


async def test_skipping_a_booked_day_cancels_and_refunds(db, cleanup, rig):
    res = await _resident(db, cleanup, rig)
    service = SocietyScheduleService(db)
    tomorrow = (now_ist().date() + timedelta(days=1)).isoformat()
    created = await service.create_visit(await _society(db, rig.sid), VisitCreateRequest(
        date=tomorrow, slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)
    await service.generate(created["id"])
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(res["subs"][0])}))["remaining_service_count"] == 1
    visit = await db.society_visits.find_one({"_id": ObjectId(created["id"])})
    await service.skip_visit(visit, rig.user)
    assert (await db.society_visits.find_one({"_id": ObjectId(created["id"])}))["status"] == "skipped"
    assert await db.bookings.count_documents({"society_visit_id": created["id"], "status": "cancelled"}) == 1
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(res["subs"][0])}))["remaining_service_count"] == 2


async def test_society_rule_and_rotation_build_visit_days(db, cleanup, rig):
    service = SocietyScheduleService(db)
    society = await _society(db, rig.sid)
    nxt_sat = now_ist().date() + timedelta(days=(5 - now_ist().date().weekday()) % 7 or 7)
    rule = await service.create_rule(society, SocietyRuleRequest(
        pattern=RulePattern(kind="every_n_weeks", weekday=5, interval_weeks=2, anchor_date=nxt_sat.isoformat()),
        slot_keys=["08:00-11:00", "11:00-14:00"], captain_ids=[rig.captain, rig.captain2], washes_per_captain=6,
    ), "society", rig.manager)
    dates = [v["date"] for v in await db.society_visits.find({"rule_id": rule["id"]}).sort("date", 1).to_list(None)]
    assert dates[0] == nxt_sat.isoformat() and all((date.fromisoformat(b) - date.fromisoformat(a)).days == 14 for a, b in zip(dates, dates[1:]))
    # A rotation replaces it (and re-plans the not-yet-booked days).
    other = await make_society(db, cleanup, rig.center, name=f"Green Acres {next(_seq)}")
    cleanup.append(("society_visits", {"society_id": other["id"]}))
    cleanup.append(("society_schedule_rules", {"society_id": other["id"]}))
    from app.schemas.society_schedule_schema import RotationRequest

    result = await service.build_rotation(rig.center["id"], RotationRequest(
        society_ids=[rig.sid, other["id"]], weekdays=[5, 6], start_date=nxt_sat.isoformat(),
        slot_keys=["08:00-11:00"], captain_ids=[rig.captain], washes_per_captain=6,
    ), rig.manager)
    assert result["created"] == 2
    assert [p["first_dates"][0] for p in result["preview"]] == [nxt_sat.isoformat(), (nxt_sat + timedelta(days=1)).isoformat()]
    assert await db.society_visits.count_documents({"rule_id": rule["id"], "status": "planned"}) == 0
    weekdays = {date.fromisoformat(v["date"]).weekday() for v in await db.society_visits.find({"society_id": other["id"]}).to_list(None)}
    assert weekdays == {6}


async def test_resident_change_request_move_is_applied_on_approval(db, cleanup, rig):
    res = await _resident(db, cleanup, rig)
    service = SocietyScheduleService(db)
    society = await _society(db, rig.sid)
    day3 = now_ist().date() + timedelta(days=3)
    await service.create_rule(society, ResidentRuleRequest(
        enrollment_id=res["enrollment"]["id"], subscription_ids=res["subs"],
        pattern=RulePattern(kind="weekly", weekday=day3.weekday()), slot_key="08:00-11:00", captain_id=rig.captain,
    ), "resident", rig.manager)
    mine = await service.my_schedule(res["customer_id"])
    first = mine["items"][0]
    assert first["date"] == day3.isoformat() and first["slot_label"] == "8:00 AM – 11:00 AM"
    assert mine["next_by_subscription"][res["subs"][0]]["date"] == day3.isoformat()
    want = (day3 + timedelta(days=1)).isoformat()
    req = await service.create_request(res["customer_id"], ChangeRequestCreate(visit_id=first["visit_id"], kind="move", preferred_date=want, preferred_slot="11:00-14:00"))
    assert req["status"] == "pending"
    with pytest.raises(Exception, match="already asked"):
        await service.create_request(res["customer_id"], ChangeRequestCreate(visit_id=first["visit_id"], kind="skip"))
    raw = await db.society_schedule_requests.find_one({"_id": ObjectId(req["id"])})
    done = await service.approve_request(raw, ChangeRequestResolve(note="See you then"), rig.user)
    assert done["status"] == "approved"
    assert (await db.society_visits.find_one({"_id": ObjectId(first["visit_id"])}))["status"] == "skipped"
    moved = await db.society_visits.find_one({"request_id": req["id"]})
    assert moved["date"] == want and moved["slot_keys"] == ["11:00-14:00"] and moved["kind"] == "resident"
    mine = await service.my_schedule(res["customer_id"])
    assert mine["items"][0]["date"] == want
    assert await db.notifications.count_documents({"user_id": res["customer_id"], "title": "Premium wash updated"}) == 1
