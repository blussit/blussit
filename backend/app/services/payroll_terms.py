"""Captain pay terms: defaults, merging, effective-dated segments (D1, D11).
Pure functions — no database. A captain's `users.pay` holds the CURRENT
terms at the top level plus `pay_history[]`, dated segments each carrying the
terms that were in force from `effective_from`; `terms_at(pay, day)` picks the
segment for a day, so a rate or basis change never rewrites the past."""
from __future__ import annotations

from datetime import date, datetime

# The dated part of the terms — what a segment stores and `terms_at` returns.
DATED_FIELDS = (
    "basis", "salary_monthly_paise", "weekly_off_days", "paid_leaves_per_month", "petrol_per_day_paise", "extra_day_on_off", "commission",
)
DEFAULT_WEEKLY_OFF_DAYS = (5, 6)  # Saturday + Sunday (0 = Monday) — founder 2026-11-23
DEFAULT_WEEKLY_OFF = 6  # the pre-2026-11-23 single day, kept for old imports
DEFAULT_PAID_LEAVES = 0
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def off_days_of(terms: dict | None) -> list[int]:
    """The weekly-off weekdays of a terms / pay block. `weekly_off_days` wins;
    an old stored `weekly_off` int means just that day (so a captain stored with
    `weekly_off: 6` stays Sunday-only until edited); nothing stored = Sat + Sun."""
    terms = terms or {}
    days = terms.get("weekly_off_days")
    if isinstance(days, (list, tuple, set)):
        return sorted({int(d) for d in days if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
    old = terms.get("weekly_off")
    if isinstance(old, int) and not isinstance(old, bool) and 0 <= old <= 6:
        return [old]
    return list(DEFAULT_WEEKLY_OFF_DAYS)


def normalize_off(pay: dict) -> dict:
    """`pay` (a copy) with `weekly_off_days` set from the old `weekly_off` when
    only that is stored, and the old key removed."""
    out = dict(pay or {})
    out["weekly_off_days"] = off_days_of(out)
    out.pop("weekly_off", None)
    return out


def weekday_labels(days: list[int]) -> str:
    return ", ".join(WEEKDAY_NAMES[d] for d in days) or "None"
DEFAULT_COMMISSION = {"mode": "percent", "percent": 0.0}
DEFAULT_MAX_COMMISSION_PERCENT = 70.0
DEFAULT_COMMISSION_ALERT_PERCENT = 40.0
DEFAULT_PAYROLL_ALERT_PAISE = 5_000_000  # ₹50,000


def empty_kit() -> dict:
    return {"provided": False, "items": [], "issued_on": None, "returned_on": None, "notes": None}


def blank_pay(basis: str = "legacy") -> dict:
    return {
        "basis": basis,
        "salary_monthly_paise": 0,
        "weekly_off_days": list(DEFAULT_WEEKLY_OFF_DAYS),
        "paid_leaves_per_month": DEFAULT_PAID_LEAVES,
        "petrol_per_day_paise": 0,
        "extra_day_on_off": False,
        "commission": dict(DEFAULT_COMMISSION),
        "deposit_expected_paise": 0,
        "kit": empty_kit(),
    }


def basis_of(user: dict | None, day: str | None = None) -> str:
    """The captain's basis today (or on `day`). A captain with no pay block
    (never backfilled) is legacy. With dated segments the one in force wins,
    so a change that starts next week isn't applied this week."""
    pay = (user or {}).get("pay") or {}
    if pay.get("pay_history"):
        from app.utils.timezone import now_ist

        return terms_at(user, day or now_ist().date().isoformat()).get("basis") or "legacy"
    return pay.get("basis") or "legacy"


def pay_view(user: dict, day: str | None = None) -> dict:
    """The pay block as it stands on a day: stored top level with the dated
    fields replaced by the segment in force."""
    pay = normalize_off(dict(user.get("pay") or blank_pay()))
    if pay.get("pay_history"):
        from app.utils.timezone import now_ist

        pay.update(terms_at(user, day or now_ist().date().isoformat()))
    return pay


def is_payroll(user: dict | None) -> bool:
    """True for a salary / commission captain — the ones D2 takes off the per-job wallet earning."""
    return basis_of(user) in ("salary", "commission")


def dated_terms(pay: dict) -> dict:
    """Only the effective-dated fields of a pay block, copied."""
    out = {k: pay.get(k) for k in DATED_FIELDS if k != "weekly_off_days"}
    out["weekly_off_days"] = off_days_of(pay)
    out["commission"] = dict(pay.get("commission") or DEFAULT_COMMISSION)
    return out


def _day(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def start_day(user: dict) -> str:
    """The captain's first day: pay.start_date, else the day the account was created (IST)."""
    pay = user.get("pay") or {}
    if pay.get("start_date"):
        return pay["start_date"]
    created = user.get("created_at")
    if isinstance(created, datetime):
        from app.utils.timezone import from_stored

        return from_stored(created).date().isoformat()
    return "2000-01-01"


def segments(user: dict) -> list[dict]:
    """[{effective_from, terms}] oldest first; always at least one (the current terms)."""
    pay = user.get("pay") or {}
    rows = [{"effective_from": h.get("effective_from") or start_day(user), "terms": h.get("terms") or {}} for h in (pay.get("pay_history") or [])]
    rows.sort(key=lambda r: r["effective_from"])
    if not rows:
        rows = [{"effective_from": start_day(user), "terms": dated_terms(pay or blank_pay())}]
    return rows


def terms_at(user: dict, day: str) -> dict:
    """The terms in force on an IST day. A day before the first segment uses the first."""
    chosen = None
    for seg in segments(user):
        if seg["effective_from"] <= day:
            chosen = seg
    terms = (chosen or segments(user)[0])["terms"] or {}
    base = blank_pay()
    merged = {k: terms.get(k, base[k]) for k in DATED_FIELDS if k != "weekly_off_days"}
    merged["weekly_off_days"] = off_days_of(terms)
    merged["commission"] = {**DEFAULT_COMMISSION, **(merged.get("commission") or {})}
    return merged


def merged_terms(current: dict, patch: dict) -> dict:
    """`current` pay block with the sent fields of `patch` laid over it
    (nested commission and kit merge field-wise)."""
    out = {**blank_pay(current.get("basis") or "legacy"), **{k: v for k, v in current.items() if v is not None}}
    out = normalize_off(out)
    patch = dict(patch)
    old_day = patch.pop("weekly_off", None)  # the old single-day field, still accepted
    if old_day is not None and patch.get("weekly_off_days") is None:
        patch["weekly_off_days"] = [old_day]
    for key, value in patch.items():
        if value is None:
            continue
        if key == "weekly_off_days":
            out["weekly_off_days"] = sorted({int(d) for d in value})
        elif key == "commission":
            out["commission"] = {**DEFAULT_COMMISSION, **(out.get("commission") or {}), **{k: v for k, v in value.items() if v is not None}}
        elif key == "kit":
            kit = {**empty_kit(), **(out.get("kit") or {})}
            kit.update({k: v for k, v in value.items() if k in ("provided", "items", "issued_on", "notes")})
            out["kit"] = kit
        else:
            out[key] = value
    return out


def terms_diff(before: dict, after: dict) -> dict:
    """{field: {before, after}} over the dated fields plus deposit and kit."""
    changes: dict = {}
    for key in (*DATED_FIELDS, "deposit_expected_paise", "kit"):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = {"before": old, "after": new}
    return changes


def commission_percent_at(user: dict, day: str) -> tuple[str, float]:
    """(mode, percent) of the commission terms on a day."""
    c = terms_at(user, day).get("commission") or {}
    return c.get("mode") or "percent", float(c.get("percent") or 0.0)


def leave_on_off_day_message(user: dict, day: str) -> str | None:
    """Why leave can't be taken on `day` (an IST YYYY-MM-DD) — it is one of a
    salary captain's weekly-off days — else None. Weekly off only exists for
    salary captains (a commission captain isn't paid by the day)."""
    try:
        weekday = date.fromisoformat(str(day)[:10]).weekday()
    except ValueError:
        return None
    if basis_of(user, str(day)[:10]) != "salary":
        return None
    if weekday in terms_at(user, str(day)[:10])["weekly_off_days"]:
        return f"{WEEKDAY_NAMES[weekday]} is a weekly off — leave can't be taken on a weekly off day."
    return None
