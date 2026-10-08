"""
The society plan MONTH (docs/SOCIETY_PLANS.md §2): a plan month runs to the
same day next month, and a full-month ("daily") plan's bucket washes follow
that month's length — days in the cycle minus Sundays, capped at the plan's
25 — while 10/15/20-day plans keep their fixed count.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services.society_month import add_month, bucket_allowance, plan_month_end, weekly_offs
from app.services.society_service import SocietyService
from app.utils.timezone import IST, now_ist

from tests.society_factories import activate_cash, auth, client, enroll, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio


async def test_plan_month_runs_to_the_same_day_next_month():
    assert add_month(date(2027, 2, 5)) == date(2027, 3, 5)
    assert add_month(date(2027, 1, 31)) == date(2027, 2, 28)
    assert add_month(date(2028, 1, 31)) == date(2028, 2, 29)  # leap year
    assert add_month(date(2026, 12, 15)) == date(2027, 1, 15)
    start = datetime(2027, 2, 5, 10, 30, tzinfo=IST)
    assert plan_month_end(start) == datetime(2027, 3, 5, 10, 30, tzinfo=IST)
    assert (plan_month_end(start) - start).days == 28
    # A naive stored instant is UTC (Mongo): 5 Mar 2027 05:00 UTC = 10:30 IST.
    assert plan_month_end(datetime(2027, 3, 5, 5, 0)) == datetime(2027, 4, 5, 10, 30, tzinfo=IST)


async def test_weekly_offs_counts_sundays_in_the_window():
    # 1-28 Feb 2027: Sundays 7, 14, 21, 28.
    assert weekly_offs(date(2027, 2, 1), date(2027, 3, 1)) == 4
    # [Sun 7 Feb, Sun 14 Feb) has exactly one Sunday.
    assert weekly_offs(date(2027, 2, 7), date(2027, 2, 14)) == 1
    assert weekly_offs(date(2027, 2, 8), date(2027, 2, 14)) == 0
    assert weekly_offs(date(2027, 2, 8), date(2027, 2, 8)) == 0


async def test_full_month_allowance_follows_the_month_length():
    # February (28 days, 4 Sundays) -> 24; the daily plan covers fewer days.
    assert bucket_allowance(25, date(2027, 2, 5), date(2027, 3, 5)) == 24
    # 30-day and 31-day months -> the plan's 25 (cap).
    assert bucket_allowance(25, date(2027, 4, 5), date(2027, 5, 5)) == 25
    assert bucket_allowance(25, date(2027, 3, 5), date(2027, 4, 5)) == 25
    # A 29-day leap February with 5 Sundays (1 Feb 2032 is a Sunday) -> 24.
    assert bucket_allowance(25, date(2032, 2, 1), date(2032, 3, 1)) == 24
    # 10 / 15 / 20-day plans stay fixed whatever the month.
    for days in (10, 15, 20):
        assert bucket_allowance(days, date(2027, 2, 5), date(2027, 3, 5)) == days
        assert bucket_allowance(days, date(2027, 3, 5), date(2027, 4, 5)) == days


async def test_activation_starts_a_calendar_plan_month(db, cleanup):
    center = await make_center(db, cleanup, spot=3)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, society, resident, plan["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    sub = await db.user_subscriptions.find_one({"_id": ObjectId(enrollment["cars"][0]["subscription"]["id"])})
    start = sub["start_date"].replace(tzinfo=timezone.utc)
    expected = plan_month_end(start).astimezone(timezone.utc).replace(tzinfo=None)
    assert abs((sub["end_date"] - expected).total_seconds()) < 2


async def test_captain_checklist_uses_the_february_allowance(db, cleanup):
    center = await make_center(db, cleanup, spot=4)
    society = await make_society(db, cleanup, center)
    _manager, captain = await staff(db, cleanup, center["id"])
    service = SocietyService(db)
    await service.set_captain(await service.societies.find_by_id(society["id"]), captain, None)
    plan = await make_template(db, cleanup, bucket_days=25)
    resident = await make_resident(db, cleanup)
    view = await enroll(db, society, resident, plan["id"], cars=1)
    enrollment = (await activate_cash(db, view["id"]))["enrollment"]
    vehicle = enrollment["cars"][0]["vehicle_id"]
    # Pretend this plan month is a 28-day one (with 4 Sundays) that contains
    # today: 28 days with today somewhere in the middle.
    today = now_ist().date()
    start = today - timedelta(days=10)
    end = start + timedelta(days=28)
    sundays = weekly_offs(start, end)
    expected = min(25, 28 - sundays)
    await db.user_subscriptions.update_one(
        {"vehicle_id": vehicle, "society_id": society["id"]},
        {"$set": {
            "cycle_start": datetime.combine(start, datetime.min.time(), tzinfo=IST),
            "start_date": datetime.combine(start, datetime.min.time(), tzinfo=IST),
            "end_date": datetime.combine(end, datetime.min.time(), tzinfo=IST),
        }},
    )
    h = auth(captain, "captain", center["id"])
    async with client() as c:
        card = (await c.get("/api/v1/societies/captain/today", headers=h)).json()["data"]["societies"][0]
        car = next(x for x in card["cars"] if x["vehicle_id"] == vehicle)
        assert car["allowance"] == expected == 24
    # The manager's residents list shows the same allowance.
    rows = await service.enrollments_for_society(await service.societies.find_by_id(society["id"]))
    assert rows[0]["cars"][0]["bucket_allowance"] == 24
