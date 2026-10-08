"""FINAL-POLISH (2026-10-08): pass dates read "6 Nov 2026" — never
"06 Nov 2026" — through one helper (`app.utils.timezone.day_label`) for
every label and message of the pass round: `last_booking_day_label`,
`starts_on_label`, `renewal_starts_on_label`, "Last Booking Day: …",
"Extended by … — Last Booking Day: …", "Your plan continues on …"."""
from datetime import date, datetime, timedelta, timezone

from app.services.society_service import _last_day_label
from app.services.subscription_service import (
    PASS_SCHEDULED,
    _with_effective_status,
    extension_message,
    last_booking_day_text,
    renewal_continues_text,
    usable_until_label,
)
from app.utils.timezone import IST, day_label, now_ist


def test_day_label_has_no_leading_zero():
    assert day_label(date(2026, 11, 6)) == "6 Nov 2026"
    assert day_label(datetime(2026, 11, 6, 23, 0, tzinfo=IST)) == "6 Nov 2026"
    assert day_label("2026-11-06") == "6 Nov 2026"
    assert day_label("2026-11-16T00:00:00+05:30") == "16 Nov 2026"
    assert day_label(date(2026, 11, 6), year=False) == "6 Nov"
    assert day_label(None) is None and day_label("") is None


def test_pass_labels_and_messages_use_it():
    # Period ends 00:00 IST 7 Nov → the last booking day is 6 Nov.
    end = datetime(2026, 11, 7, 0, 0, tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
    sub = {"end_date": end, "status": "active"}
    assert usable_until_label(sub) == "6 Nov 2026"
    assert last_booking_day_text(sub) == "Last Booking Day: 6 Nov 2026"
    assert extension_message(4, {"last_bookable_day": "2026-11-06"}) == "Extended by 4 days — Last Booking Day: 6 Nov 2026"
    assert renewal_continues_text(datetime(2026, 11, 7, 0, 0, tzinfo=IST)) == "Your plan continues on 7 Nov 2026 — next period already paid."
    assert _last_day_label(datetime(2026, 11, 7, 0, 0, tzinfo=IST)) == "6 Nov 2026"


def test_scheduled_pass_starts_on_label():
    start = (now_ist() + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    view = _with_effective_status({"_id": "x", "status": PASS_SCHEDULED, "start_date": start, "end_date": start + timedelta(days=30)})
    assert view["starts_on_label"] == f"{start.day} {start.strftime('%b %Y')}"
    assert not view["starts_on_label"].startswith("0")
