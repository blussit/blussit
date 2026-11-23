"""Salary month maths (§7.13 D9–D11, H5, H6, H20): a table of cases against the
pure engine, then the HTTP surface — attendance override, statement, finalize."""
from datetime import date, timedelta
from fractions import Fraction

import pytest
from bson import ObjectId

from app.services.payroll_salary import compute_month, previous_month, round_paise, working_days_in_month, month_days
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, dues, ledger, make_payroll, rig, second_center

pytestmark = pytest.mark.asyncio

OCT = "2026-10"  # 31 days, 4 Sundays (4, 11, 18, 25) → 27 working days
TODAY = "2026-11-23"
SALARY = 2_700_000  # ₹27,000 → exactly ₹1,000 a working day


def user(**pay) -> dict:
    base = {
        "basis": "salary", "salary_monthly_paise": SALARY, "weekly_off": 6, "paid_leaves_per_month": 1, "petrol_per_day_paise": 0,
        "extra_day_on_off": False, "commission": {"mode": "percent", "percent": 0}, "start_date": "2026-01-01", "pay_history": [],
    }
    base.update(pay)
    return {"_id": ObjectId(), "pay": base}


def working_days(month: str = OCT) -> list[str]:
    return [d.isoformat() for d in month_days(month) if d.weekday() != 6]


def att(day: str, status="present", **kw) -> dict:
    return {"attendance_date": day, "status": status, **kw}


def run(u, *, attendance=None, jobs=(), leaves=(), holidays=(), month=OCT, today=TODAY):
    return compute_month(u, month, today, {a["attendance_date"]: a for a in (attendance or [])}, set(jobs), set(leaves), set(holidays))


def row(result, day: str) -> dict:
    return next(d for d in result["days"] if d["date"] == day)


def test_working_days_and_day_rate():
    assert working_days_in_month(month_days(OCT), 6) == 27
    r = run(user())
    assert r["expected_working_days"] == 27 and r["day_rate_paise"] == 100_000


def test_full_attendance_pays_the_whole_salary():
    r = run(user(), attendance=[att(d) for d in working_days()])
    assert r["computed_paise"] == SALARY and r["paid_days"] == 27 and r["present_days"] == 27 and r["absent_days"] == 0


def test_nobody_checked_in_means_absent_and_no_pay_but_weekly_offs_are_not_absences():
    r = run(user())
    assert r["computed_paise"] == 0 and r["absent_days"] == 27
    assert row(r, "2026-10-04")["status"] == "weekly_off"


def test_jobs_count_as_presence_without_a_check_in():
    r = run(user(), jobs=working_days()[:5])
    assert r["computed_paise"] == 5 * 100_000 and row(r, working_days()[0])["source"] == "job"


def test_half_day_pays_half():
    days = working_days()
    r = run(user(), attendance=[att(d) for d in days[:2]] + [att(days[2], "half_day", marked_by="m", override_note="left early")])
    assert r["computed_paise"] == 250_000 and r["half_days"] == 1 and r["paid_days"] == 2.5


def test_mid_month_join_pays_only_from_the_start_day():
    u = user(start_date="2026-10-16")
    after = [d for d in working_days() if d >= "2026-10-16"]
    assert len(after) == 14
    r = run(u, attendance=[att(d) for d in working_days()])  # rows before the join are ignored
    assert row(r, "2026-10-15")["status"] == "not_joined"
    assert r["computed_paise"] == 14 * 100_000 and r["absent_days"] == 0


def test_a_captain_who_left_is_paid_to_the_last_day():
    u = user(end_date="2026-10-10")
    r = run(u, attendance=[att(d) for d in working_days()])
    assert row(r, "2026-10-11")["status"] == "left"
    assert r["computed_paise"] == len([d for d in working_days() if d <= "2026-10-10"]) * 100_000


def test_approved_leave_is_paid_up_to_the_allowance_then_unpaid():
    days = working_days()
    leaves = days[:3]
    r = run(user(paid_leaves_per_month=1), attendance=[att(d) for d in days[3:]], leaves=leaves)
    assert r["leave_days"] == 3 and r["computed_paise"] == (24 + 1) * 100_000
    assert [row(r, d)["paid_leave"] for d in leaves] == [True, False, False]
    r2 = run(user(paid_leaves_per_month=3), attendance=[att(d) for d in days[3:]], leaves=leaves)
    assert r2["computed_paise"] == 27 * 100_000


def test_leave_over_a_sunday_burns_no_allowance():
    r = run(user(), attendance=[att(d) for d in working_days() if d > "2026-10-05"], leaves=["2026-10-04", "2026-10-05"])
    assert row(r, "2026-10-04")["status"] == "weekly_off" and row(r, "2026-10-05")["paid_leave"] is True


def test_an_explicit_paid_leave_is_always_paid():
    days = working_days()
    marks = [att(d, "leave", marked_by="m", paid_leave=True) for d in days[:3]]
    r = run(user(paid_leaves_per_month=1), attendance=marks + [att(d) for d in days[3:]])
    assert r["computed_paise"] == SALARY


def test_admin_holiday_is_paid_even_with_no_attendance():
    days = working_days()
    r = run(user(), attendance=[att(d) for d in days[2:]], holidays=days[:2])
    assert r["holidays"] == 2 and r["computed_paise"] == SALARY


def test_manager_absent_override_beats_a_check_in():
    days = working_days()
    r = run(user(), attendance=[att(d) for d in days[:-1]] + [att(days[-1], "absent", marked_by="m", override_note="Never came")])
    assert row(r, days[-1])["status"] == "absent" and row(r, days[-1])["overridden"] is True
    assert r["computed_paise"] == 26 * 100_000


def test_manager_present_override_fixes_a_forgotten_check_in():
    r = run(user(), attendance=[att("2026-10-06", "present", marked_by="m", override_note="Forgot to check in")])
    assert r["computed_paise"] == 100_000 and row(r, "2026-10-06")["source"] == "manual"


def test_working_the_weekly_off_adds_a_day_only_when_the_terms_say_so():
    days = working_days() + ["2026-10-04"]
    r = run(user(), attendance=[att(d) for d in days])
    assert r["computed_paise"] == SALARY and row(r, "2026-10-04")["worked_off_day"] is True
    r2 = run(user(extra_day_on_off=True), attendance=[att(d) for d in days])
    assert r2["computed_paise"] == SALARY + 100_000


def test_different_weekly_off_changes_the_denominator():
    u = user(weekly_off=0)  # Monday off: Oct 2026 has 4 Mondays (5, 12, 19, 26)
    r = run(u, attendance=[att(d.isoformat()) for d in month_days(OCT) if d.weekday() != 0])
    assert r["expected_working_days"] == 27 and r["computed_paise"] == SALARY


def test_today_with_no_evidence_is_pending_not_absent_and_the_future_is_upcoming():
    r = run(user(), month="2026-11", today="2026-11-10", attendance=[att("2026-11-02")])
    assert row(r, "2026-11-10")["status"] == "pending" and row(r, "2026-11-11")["status"] == "upcoming"
    assert row(r, "2026-11-09")["status"] == "absent"
    assert r["computed_paise"] == round_paise(Fraction(SALARY, working_days_in_month(month_days("2026-11"), 6)))


def test_petrol_accrues_per_present_day_for_salary_and_commission_alike():
    days = working_days()
    r = run(user(petrol_per_day_paise=5000), attendance=[att(d) for d in days[:4]] + [att(days[4], "half_day", marked_by="m")])
    assert r["petrol_paise"] == 4 * 5000 + 2500
    r2 = run(user(basis="commission", petrol_per_day_paise=5000), attendance=[att(d) for d in days[:4]])
    assert r2["computed_paise"] == 0 and r2["petrol_paise"] == 20000


def test_basis_switch_mid_month_pays_each_segment_under_its_own_terms():
    u = user()
    u["pay"]["pay_history"] = [
        {"effective_from": "2026-01-01", "terms": {**{k: u["pay"][k] for k in ("salary_monthly_paise", "weekly_off", "paid_leaves_per_month", "petrol_per_day_paise", "extra_day_on_off", "commission")}, "basis": "salary"}},
        {"effective_from": "2026-10-16", "terms": {"basis": "commission", "salary_monthly_paise": 0, "weekly_off": 6, "paid_leaves_per_month": 1, "petrol_per_day_paise": 0, "extra_day_on_off": False, "commission": {"mode": "percent", "percent": 30}}},
    ]
    r = run(u, attendance=[att(d) for d in working_days()])
    assert r["computed_paise"] == len([d for d in working_days() if d < "2026-10-16"]) * 100_000


def test_rounding_is_exact_over_the_month():
    # ₹10,000 over 26 working days does not divide; the full month still adds back to the salary
    u = user(salary_monthly_paise=1_000_000)
    days = [d.isoformat() for d in month_days("2026-11") if d.weekday() != 6]
    r = run(u, month="2026-11", today="2026-12-05", attendance=[att(d) for d in days])
    assert r["computed_paise"] == 1_000_000
    r2 = run(u, month="2026-11", today="2026-12-05", attendance=[att(d) for d in days[:7]])
    assert r2["computed_paise"] == round_paise(Fraction(1_000_000 * 7, len(days)))


# ------------------------------------------------------------------ HTTP: attendance, statement, finalize


def last_month() -> str:
    from app.utils.timezone import now_ist

    return previous_month(now_ist().date().isoformat()[:7])


async def mark(c, s, day: str, status="present", **kw):
    return await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day, "status": status, "note": kw.pop("note", "Checked by manager"), **kw}, headers=s["mgr"]["h"])


async def test_attendance_override_rules_and_audit(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=SALARY)
        month = last_month()
        day = working_days(month)[0]
        r = await mark(c, s, day)
        assert r.status_code == 200 and r.json()["data"]["status"] == "present" and r.json()["data"]["overridden"] is True
        row_ = await db.attendance.find_one({"captain_id": s["cap"]["id"], "attendance_date": day})
        assert row_["marked_by"] == s["mgr"]["id"] and row_["marked_by_role"] == "manager" and row_["override_note"] == "Checked by manager"
        # again with another status updates the same row (unique per day)
        r = await mark(c, s, day, "half_day")
        assert r.status_code == 200 and await db.attendance.count_documents({"captain_id": s["cap"]["id"], "attendance_date": day}) == 1
        # future days, days before the captain started, another center, a note-less call
        assert (await mark(c, s, h.day(1))).status_code == 400
        await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$set": {"pay.start_date": h.day(-5)}})
        assert (await mark(c, s, h.day(-6))).status_code == 400
        assert (await mark(c, s, h.day(-5))).status_code == 200
        r = await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day, "status": "present", "note": "ok then"}, headers=other["mgr"]["h"])
        assert r.status_code == 403
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day, "status": "present", "note": ""}, headers=s["mgr"]["h"])).status_code == 422
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/attendance", json={"date": day, "status": "present", "note": "x y z"}, headers=s["cap"]["h"])).status_code == 403
    audit = await db.audit_logs.find_one({"action": "PAYROLL_ATTENDANCE", "target_id": s["cap"]["id"]})
    assert audit and audit["service_center_id"] == s["center_id"]


async def test_statement_and_finalize_twice_posts_only_the_difference(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=SALARY, petrol_per_day_paise=5000, effective_from=last_month() + "-01")
        month = last_month()
        days = working_days(month)
        expected_days = len(days)
        per_day = Fraction(SALARY, expected_days)
        for d in days[:3]:
            assert (await mark(c, s, d)).status_code == 200
        st = (await c.get(f"{API}/captains/{s['cap']['id']}/statement", params={"month": month}, headers=s["mgr"]["h"])).json()["data"]
        assert st["salary"]["computed_paise"] == round_paise(per_day * 3) and st["salary"]["finalized"] is False
        assert st["salary"]["petrol_paise"] == 15000 and st["salary"]["present_days"] == 3
        assert len(st["attendance"]) == len(month_days(month))
        fin = f"{API}/captains/{s['cap']['id']}/months/{month}/finalize"
        r = await c.post(fin, json={}, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        first = round_paise(per_day * 3)
        assert [(x["kind"], x["delta"]) for x in r.json()["data"]["posted"]] == [("salary_accrual", first), ("allowance", 15000)]
        assert await dues(db, s["cap"]["id"]) == first + 15000
        # nothing changed → nothing more is posted
        r = await c.post(fin, json={}, headers=s["mgr"]["h"])
        assert r.status_code == 200 and r.json()["data"]["posted"] == [] and r.json()["data"]["finalized"] is True
        assert len(await ledger(db, s["cap"]["id"])) == 2
        # a backdated attendance fix → re-finalize posts the difference (one more row, own key)
        assert (await mark(c, s, days[3])).status_code == 200
        r = await c.post(fin, json={}, headers=s["mgr"]["h"])
        second = round_paise(per_day * 4)
        assert [(x["kind"], x["delta"]) for x in r.json()["data"]["posted"]] == [("salary_accrual", second - first), ("allowance", 5000)]
        rows = await ledger(db, s["cap"]["id"], kind="salary_accrual")
        assert [x["key"] for x in rows] == [f"sal:{s['cap']['id']}:{month}", f"sal:{s['cap']['id']}:{month}:1"]
        assert await dues(db, s["cap"]["id"]) == second + 20000
        # override with a reason: payable becomes the typed amount, difference-only again
        r = await c.post(fin, json={"override_amount_paise": 2_000_000}, headers=s["mgr"]["h"])
        assert r.status_code == 400
        r = await c.post(fin, json={"override_amount_paise": 2_000_000, "reason": "Agreed full pay for a good month"}, headers=s["mgr"]["h"])
        assert r.status_code == 200
        assert await dues(db, s["cap"]["id"]) == 2_000_000 + 20000
        st = (await c.get(f"{API}/captains/{s['cap']['id']}/statement", params={"month": month}, headers=s["mgr"]["h"])).json()["data"]
        assert st["salary"]["finalized"] is True and st["salary"]["payable_paise"] == 2_000_000 and st["salary_paise"] == 2_000_000
        # a future month, a month before they started, a bad month
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/months/2099-01/finalize", json={}, headers=s["mgr"]["h"])).status_code == 400
        assert (await c.post(f"{API}/captains/{s['cap']['id']}/months/2026-13/finalize", json={}, headers=s["mgr"]["h"])).status_code in (400, 422)


async def test_finalize_races_land_once(db):
    import asyncio

    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=SALARY, effective_from=last_month() + "-01")
        month = last_month()
        for d in working_days(month)[:2]:
            await mark(c, s, d)
        fin = f"{API}/captains/{s['cap']['id']}/months/{month}/finalize"
        results = await asyncio.gather(*[c.post(fin, json={}, headers=s["mgr"]["h"]) for _ in range(3)])
        assert all(r.status_code in (200, 409) for r in results)
    rows = await ledger(db, s["cap"]["id"], kind="salary_accrual")
    assert len(rows) == 1


async def test_holiday_and_approved_leave_feed_the_statement(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=SALARY, effective_from=last_month() + "-01")
        month = last_month()
        days = working_days(month)
        from datetime import datetime, timezone
        from app.utils.timezone import IST

        start = datetime.fromisoformat(days[0]).replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
        await db.holidays.insert_one({"active": True, "scope": "all", "service_center_ids": [], "start_at": start, "end_at": start + timedelta(days=1) - timedelta(milliseconds=1), "is_deleted": False})
        await db.leave_requests.insert_one({"captain_id": s["cap"]["id"], "status": "approved", "start_date": days[1], "end_date": days[1], "reason": "x", "is_deleted": False})
        st = (await c.get(f"{API}/captains/{s['cap']['id']}/statement", params={"month": month}, headers=s["mgr"]["h"])).json()["data"]
        rows = {d["date"]: d for d in st["attendance"]}
        assert rows[days[0]]["status"] == "holiday" and rows[days[1]]["status"] == "leave" and rows[days[1]]["paid_leave"] is True
        assert st["salary"]["holidays"] == 1 and st["salary"]["leave_days"] == 1
    await db.holidays.delete_many({"start_at": start})
    await db.leave_requests.delete_many({"captain_id": s["cap"]["id"]})
