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
