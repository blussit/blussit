"""
Whole-rupee money helpers.

Every discount a customer or manager sees is a whole rupee — ₹35 off, never
₹34.90, and a ₹50 discount on a two-car visit is ₹21 + ₹29, never ₹20.58 +
₹29.42. Rounding is half-up (₹34.50 -> ₹35), the way people round by hand.
"""
from decimal import ROUND_HALF_UP, Decimal


def round_rupees(amount: float | int | None) -> int:
    """Half-up to the nearest whole rupee (Python's round() is banker's)."""
    return int(Decimal(str(amount or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def split_whole_rupees(amount: int, weights: list[float], caps: list[float] | None = None) -> list[int]:
    """Split a whole-rupee `amount` across parts in proportion to `weights`
    (largest remainder), so the parts are whole rupees that add up to
    exactly `amount`. `caps` (optional) bounds each part — a car's discount
    can't exceed its own price; anything a capped part can't take moves to
    the others."""
    n = len(weights)
    if n == 0:
        return []
    amount = max(0, int(amount))
    caps_int = [max(0, int(c // 1)) for c in caps] if caps is not None else [amount] * n
    amount = min(amount, sum(caps_int))
    total_weight = sum(max(0.0, w) for w in weights)
    if total_weight <= 0:
        weights = [1.0] * n
        total_weight = float(n)
    exact = [amount * max(0.0, w) / total_weight for w in weights]
    parts = [min(int(e // 1), caps_int[i]) for i, e in enumerate(exact)]
    left = amount - sum(parts)
    # Hand out the leftover rupees one at a time, biggest fraction first,
    # skipping parts already at their cap.
    order = sorted(range(n), key=lambda i: (exact[i] - int(exact[i] // 1)), reverse=True)
    while left > 0:
        moved = False
        for i in order:
            if left == 0:
                break
            if parts[i] < caps_int[i]:
                parts[i] += 1
                left -= 1
                moved = True
        if not moved:
            break
    return parts
