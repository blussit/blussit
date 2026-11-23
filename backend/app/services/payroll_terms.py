"""Captain pay terms: defaults, merging, effective-dated segments (D1, D11).
Pure functions — no database. A captain's `users.pay` holds the CURRENT
terms at the top level plus `pay_history[]`, dated segments each carrying the
terms that were in force from `effective_from`; `terms_at(pay, day)` picks the
segment for a day, so a rate or basis change never rewrites the past."""
from __future__ import annotations

from datetime import date, datetime

# The dated part of the terms — what a segment stores and `terms_at` returns.
DATED_FIELDS = (
    "basis", "salary_monthly_paise", "weekly_off", "paid_leaves_per_month", "petrol_per_day_paise", "extra_day_on_off", "commission",
)
DEFAULT_WEEKLY_OFF = 6  # Sunday (0 = Monday)
DEFAULT_PAID_LEAVES = 1
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
        "weekly_off": DEFAULT_WEEKLY_OFF,
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
    pay = dict(user.get("pay") or blank_pay())
    if pay.get("pay_history"):
        from app.utils.timezone import now_ist

        pay.update(terms_at(user, day or now_ist().date().isoformat()))
    return pay


def is_payroll(user: dict | None) -> bool:
    """True for a salary / commission captain — the ones D2 takes off the per-job wallet earning."""
    return basis_of(user) in ("salary", "commission")


def dated_terms(pay: dict) -> dict:
    """Only the effective-dated fields of a pay block, copied."""
    out = {k: pay.get(k) for k in DATED_FIELDS}
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
    merged = {k: terms.get(k, base[k]) for k in DATED_FIELDS}
    merged["commission"] = {**DEFAULT_COMMISSION, **(merged.get("commission") or {})}
    return merged


def merged_terms(current: dict, patch: dict) -> dict:
    """`current` pay block with the sent fields of `patch` laid over it
    (nested commission and kit merge field-wise)."""
    out = {**blank_pay(current.get("basis") or "legacy"), **{k: v for k, v in current.items() if v is not None}}
    for key, value in patch.items():
        if value is None:
            continue
        if key == "commission":
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
