"""Salary month maths (D9, D10, D11) — pure, table-testable, no database.

For one captain and one IST month it returns a day-by-day table and the
amounts they add up to. Rules, in order, for each day:

  - before the captain's start day → `not_joined`; after the end day → `left`;
  - a MANUAL day (manager/admin override row) wins: present, half_day, absent,
    leave (+ `paid_leave`);
  - evidence of work — a check-in row, or a job the captain completed/started
    that day → present;
  - the captain's weekly-off day → `weekly_off` (not in the pay at all; working
    it adds a day only when the terms say `extra_day_on_off`);
  - an admin holiday covering the whole day → `holiday` (paid);
  - an approved leave covering the day → `leave` (paid while the month's
    `paid_leaves_per_month` allowance lasts, then unpaid);
  - a day after today → `upcoming`; today with nothing yet → `pending`;
    any other past working day → `absent` (unpaid, overridable).

Salary = Σ over paid days of (monthly salary ÷ the month's working days under
the terms in force that day) — fractions are exact until the one final rounding
to a paisa, so the days of a month always add back to the salary.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from fractions import Fraction

from app.services.payroll_terms import start_day, terms_at

MANUAL_STATUSES = ("present", "half_day", "absent", "leave")


def month_days(month: str) -> list[date]:
    year, mon = int(month[:4]), int(month[5:7])
    return [date(year, mon, d) for d in range(1, calendar.monthrange(year, mon)[1] + 1)]


def working_days_in_month(days: list[date], weekly_off: int) -> int:
    return sum(1 for d in days if d.weekday() != weekly_off)


def round_paise(value: Fraction) -> int:
    """Half-up to a whole paisa (amounts here are never negative)."""
    if value <= 0:
        return 0
    return int((value * 2 + 1) // 2)


def compute_month(
    user: dict,
    month: str,
    today: str,
    attendance: dict[str, dict],
    job_days: set[str],
    leave_days: set[str],
    holiday_days: set[str],
) -> dict:
    days = month_days(month)
    start = start_day(user)
    end = (user.get("pay") or {}).get("end_date") or None
    rows: list[dict] = []
    used_leaves = 0
    present_n = Fraction(0)
    half_n = 0
    leave_n = 0
    absent_n = 0
    holiday_n = 0
    paid_days = Fraction(0)
    salary_total = Fraction(0)
    petrol_total = Fraction(0)
    last_active = None
    for d in days:
        iso = d.isoformat()
        terms = terms_at(user, iso)
        wk_off = terms["weekly_off"]
        row = {"date": iso, "status": "absent", "paid_fraction": 0.0, "overridden": False, "paid_leave": False, "note": None, "marked_by": None, "source": None}
        att = attendance.get(iso) or {}
        manual = att.get("marked_by") and att.get("status") in MANUAL_STATUSES
        if iso < start:
            row["status"] = "not_joined"
            rows.append(row)
            continue
        if end and iso > end:
            row["status"] = "left"
            rows.append(row)
            continue
        last_active = iso
        presence = Fraction(0)  # 1 full day, 1/2 half day
        pay_fraction = Fraction(0)
        is_off = d.weekday() == wk_off
        evidence = (att.get("status") == "present" and not att.get("marked_by")) or iso in job_days
        if manual:
            row.update({"overridden": True, "note": att.get("override_note"), "marked_by": att.get("marked_by"), "source": "manual"})
            status = att["status"]
            if status == "present":
                row["status"] = "present"
                presence = Fraction(1)
            elif status == "half_day":
                row["status"] = "half_day"
                presence = Fraction(1, 2)
            elif status == "absent":
                row["status"] = "weekly_off" if is_off else "absent"
            else:  # leave
                if is_off:
                    row["status"] = "weekly_off"
                elif iso in holiday_days:
                    row["status"] = "holiday"
                else:
                    row["status"] = "leave"
                    row["paid_leave"] = bool(att.get("paid_leave"))
        elif evidence:
            row.update({"status": "present", "source": "check_in" if att.get("status") == "present" else "job"})
            presence = Fraction(1)
        elif is_off:
            row["status"] = "weekly_off"
        elif iso in holiday_days:
            row["status"] = "holiday"
        elif iso in leave_days:
            row["status"] = "leave"
            row["source"] = "leave_request"
        elif iso > today:
            row["status"] = "upcoming"
        elif iso == today:
            row["status"] = "pending"
        else:
            row["status"] = "absent"

        # What each status is worth in paid days (working days only; the
        # weekly off is outside the denominator).
        if row["status"] in ("present", "half_day"):
            present_n += presence if row["status"] == "present" else 0
            half_n += 1 if row["status"] == "half_day" else 0
            if is_off:
                row["worked_off_day"] = True
                pay_fraction = Fraction(1) if (terms["extra_day_on_off"] and presence > 0) else Fraction(0)
                if terms["extra_day_on_off"] and presence == Fraction(1, 2):
                    pay_fraction = Fraction(1, 2)
            else:
                pay_fraction = presence
        elif row["status"] == "holiday":
            holiday_n += 1
            pay_fraction = Fraction(1)
        elif row["status"] == "leave":
            leave_n += 1
            if row["paid_leave"]:
                used_leaves += 1
                pay_fraction = Fraction(1)
            elif used_leaves < int(terms["paid_leaves_per_month"] or 0):
                used_leaves += 1
                row["paid_leave"] = True
                pay_fraction = Fraction(1)
        elif row["status"] == "absent":
            absent_n += 1

        row["paid_fraction"] = float(pay_fraction)
        if terms["basis"] == "salary" and pay_fraction > 0:
            expected = working_days_in_month(days, wk_off) or 1
            salary_total += Fraction(int(terms["salary_monthly_paise"] or 0), expected) * pay_fraction
            paid_days += pay_fraction
        if presence > 0 and terms["basis"] in ("salary", "commission"):
            petrol_total += Fraction(int(terms["petrol_per_day_paise"] or 0)) * presence
        rows.append(row)

    ref_terms = terms_at(user, last_active or days[-1].isoformat())
    expected_days = working_days_in_month(days, ref_terms["weekly_off"])
    salary = int(ref_terms["salary_monthly_paise"] or 0)
    return {
        "month": month,
        "days": rows,
        "expected_working_days": expected_days,
        "paid_days": round(float(paid_days), 2),
        "present_days": round(float(present_n), 2),
        "half_days": half_n,
        "leave_days": leave_n,
        "absent_days": absent_n,
        "holidays": holiday_n,
        "day_rate_paise": round_paise(Fraction(salary, expected_days)) if expected_days and ref_terms["basis"] == "salary" else 0,
        "computed_paise": round_paise(salary_total),
        "petrol_paise": round_paise(petrol_total),
    }


def previous_month(month: str) -> str:
    first = date(int(month[:4]), int(month[5:7]), 1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def month_bounds(month: str) -> tuple[str, str]:
    days = month_days(month)
    return days[0].isoformat(), days[-1].isoformat()
