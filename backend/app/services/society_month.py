"""
The society plan MONTH — see docs/SOCIETY_PLANS.md §2 ("Plan month").

A society plan is a monthly plan: its quotas cover one plan month (the
cycle), never "per day". The plan month runs from the cycle start to the
SAME day of the next calendar month (IST wall clock), clamped to that
month's last day — so a plan started 5 Feb ends 5 Mar (28 days), one started
5 Mar ends 5 Apr (31 days), and one started 31 Jan ends 28/29 Feb.

Bucket-wash allowance per plan month:
  - a FULL-MONTH plan (bucket_days >= FULL_MONTH_DAYS, i.e. the 25-day
    "daily wash") gets one bucket wash per working day of THAT plan month,
    capped at the plan's bucket_days:
        allowance = min(bucket_days, days_in_cycle - weekly_offs_in_cycle)
    where the weekly off is Sunday (the same day the missed-attendance alert
    skips). A 30/31-day month gives 25 (the cap); a 28-day February gives
    24; a 29-day month gives 24 or 25 depending on its Sundays.
  - a 10 / 15 / 20-day plan keeps its fixed count in every month.

Pure helpers — no database — so the captain checklist, the resident hub and
the manager's residents list all count the same way.
"""
import calendar
from datetime import date, datetime, timedelta

from app.utils.timezone import IST, from_stored

# A plan with at least this many bucket days a cycle is the "daily wash".
FULL_MONTH_DAYS = 25
# Python weekday of the captain's weekly off (Monday = 0 … Sunday = 6).
WEEKLY_OFF = 6


def add_month(day: date) -> date:
    """Same day next calendar month, clamped to that month's last day."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def plan_month_end(start: datetime) -> datetime:
    """When a plan month that starts at `start` ends: the same IST wall-clock
    time on the same day of the next month (clamped). `start` is a computed
    instant (aware, or naive = UTC as Mongo hands it back)."""
    local = from_stored(start) if start.tzinfo is None else start.astimezone(IST)
    nxt = add_month(local.date())
    return local.replace(year=nxt.year, month=nxt.month, day=nxt.day)


def weekly_offs(start: date, end: date) -> int:
    """Sundays in [start, end)."""
    if end <= start:
        return 0
    first = start + timedelta(days=(WEEKLY_OFF - start.weekday()) % 7)
    if first >= end:
        return 0
    return (end - first - timedelta(days=1)).days // 7 + 1


def bucket_allowance(bucket_days: int, start: date, end: date) -> int:
    """Bucket washes a car may have in the plan month [start, end)."""
    bucket_days = int(bucket_days or 0)
    if bucket_days < FULL_MONTH_DAYS:
        return bucket_days
    working = (end - start).days - weekly_offs(start, end)
    return max(0, min(bucket_days, working))


def sub_bucket_allowance(sub: dict, on: date) -> int:
    """The allowance of the plan month a bucket wash on `on` counts against
    (the previous one while an early renewal hasn't started yet)."""
    from app.services.society_service import bucket_window

    if not sub.get("end_date") or not (sub.get("cycle_start") or sub.get("start_date")):
        return int(sub.get("bucket_days") or 0)
    start, end = bucket_window(sub, on)
    return bucket_allowance(int(sub.get("bucket_days") or 0), start, end)
