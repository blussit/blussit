"""
Pure slot-generation math — no DB access, no admin config lookups, just
turning (opening time, closing time, slot duration) into a list of discrete
booking slots. Same free-function style as booking_service.py's
_slot_start_datetime/_find_window_overlap: kept here (not a method) since
it's needed from multiple services (booking creation/validation, the
customer availability endpoint, and the admin capacity screen) and has no
state of its own.

The remainder of operating hours that doesn't divide evenly into the
configured duration becomes its own final, shorter slot — never dropped,
never rounded away. E.g. 07:00-20:00 with a 180-minute duration produces
07:00-10:00, 10:00-13:00, 13:00-16:00, 16:00-19:00, 19:00-20:00.
"""


def _to_minutes(hhmm: str) -> int:
    h, m = [int(p) for p in hhmm.split(":")]
    return h * 60 + m


def _to_hhmm(total_minutes: int) -> str:
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}:{m:02d}"


def slot_key(start: str, end: str) -> str:
    return f"{start}-{end}"


def generate_slots(open_str: str, close_str: str, duration_minutes: int) -> list[dict]:
    """Returns a list of {"start": "HH:MM", "end": "HH:MM", "key": "HH:MM-HH:MM"},
    in chronological order, covering [open_str, close_str) with duration_minutes
    windows and a final short slot absorbing any remainder. Empty list if the
    window is zero/negative or duration_minutes is not positive."""
    open_m = _to_minutes(open_str)
    close_m = _to_minutes(close_str)
    if duration_minutes <= 0 or close_m <= open_m:
        return []

    slots: list[dict] = []
    cursor = open_m
    while cursor < close_m:
        end = min(cursor + duration_minutes, close_m)
        start_str = _to_hhmm(cursor)
        end_str = _to_hhmm(end)
        slots.append({"start": start_str, "end": end_str, "key": slot_key(start_str, end_str)})
        cursor = end
    return slots


def format_time_12h(hhmm: str, compact: bool = False) -> str:
    """"14:30" -> "2:30 PM" (IST wall-clock, 12-hour) for anything a person
    reads. compact drops ":00" ("9 AM") for tight spots like WhatsApp list
    titles. Anything unparseable comes back unchanged."""
    try:
        h, m = [int(p) for p in str(hhmm).strip().split(":")[:2]]
    except ValueError:
        return str(hhmm)
    period = "PM" if h >= 12 else "AM"
    hour12 = h % 12 or 12
    return f"{hour12} {period}" if compact and m == 0 else f"{hour12}:{m:02d} {period}"


def format_slot_12h(slot: str | None, compact: bool = False) -> str:
    """"09:00-12:00" -> "9:00 AM – 12:00 PM". The stored key stays 24-hour
    (it's an identifier); this is display only."""
    text = str(slot or "").strip()
    if "-" not in text:
        return text
    start, end = [part.strip() for part in text.split("-", 1)]
    sep = "–" if compact else " – "
    return f"{format_time_12h(start, compact)}{sep}{format_time_12h(end, compact)}"
