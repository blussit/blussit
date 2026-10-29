"""Plain-English wording for API success messages, so every endpoint says
WHAT changed (which booking, how much, when) instead of a bare "Success".
Amounts are whole rupees ("₹50") unless a real paise part exists."""
from datetime import date, datetime

from app.utils.slots import format_slot_12h
from app.utils.timezone import day_label


def rupees(amount) -> str:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return "₹0"
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}₹{value:.0f}" if abs(value - round(value)) < 0.005 else f"{sign}₹{value:.2f}"


def plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def when_text(day: date | datetime | str | None, slot: str | None) -> str:
    """"30 Oct 2026, 9:00 AM – 12:00 PM" from a booking's day + slot key."""
    parts = [p for p in (day_label(day) if day else None, format_slot_12h(slot) if slot else None) if p]
    return ", ".join(parts)


def customer_wallet_line(line: str | None, by_staff: bool) -> str:
    """The wallet line the money service writes addresses the customer
    ("added to your Blussit wallet"); a staff caller is told whose wallet."""
    text = (line or "").strip()
    if text and by_staff:
        text = text.replace("your Blussit wallet", "the customer's Blussit wallet").replace("your next booking", "their next booking")
    return text
