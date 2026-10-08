"""
What a booking costs, what was paid and what is still due — pure helpers,
no I/O (spec docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md §1.5).

Booking money fields:
  total_amount      what the visit costs: services + travel + anything
                    carried from the wallet (wallet_due_carried) + on-site
                    add-ons + legacy cancellation_charge
  wallet_applied    customer-wallet credit spent on it at creation
  amount_paid       money received for it (online, QR, cash) — capped at
                    what it costs; anything beyond goes to the wallet
  amount_due        max(0, total − wallet_applied − amount_paid)
  payment_status    pending | partially_paid | paid | refund_due | refunded

Bookings written before the wallet existed carry none of the new fields:
`amount_paid` is then the whole total when the booking was paid (or its
money is owed back / returned) and 0 otherwise, `wallet_applied` is 0.
Every reader goes through these helpers, so an old booking never needs a
migration to read correctly (`backfill_booking_money` in money_service
materialises the fields anyway, once, so `$inc` has a base).

The captain wallet nets, per booking, to
    captain_earning (once the job is completed) − cash the captain holds
and `captain_wallet_posted` records how much of that is already posted —
see MoneyService.settle_captain_wallet.
"""
from __future__ import annotations

PENDING = "pending"
PARTIALLY_PAID = "partially_paid"
PAID = "paid"
REFUND_DUE = "refund_due"
REFUNDED = "refunded"

# A stored status that is not derived from the amounts: the money went back
# (or is owed back) — the booking was cancelled or deleted.
_TERMINAL = frozenset({REFUND_DUE, REFUNDED})
# Legacy statuses that meant "the whole total was received".
_LEGACY_RECEIVED = frozenset({PAID, REFUND_DUE, REFUNDED})

EPSILON = 0.004


def r2(value) -> float:
    """Rupees to 2 decimals (money is stored as rupee floats)."""
    try:
        return round(float(value or 0) + 0.0, 2)
    except (TypeError, ValueError):
        return 0.0


def paise(value) -> int:
    return int(round(float(value or 0) * 100))


def total_of(booking: dict) -> float:
    return r2((booking or {}).get("total_amount"))


def wallet_applied_of(booking: dict) -> float:
    return r2((booking or {}).get("wallet_applied"))


def amount_paid_of(booking: dict) -> float:
    """Money received for this booking (applied to it). A booking from
    before partial payments (no `amount_paid`) received its whole total iff
    it was paid. A stored "paid" always means fully paid: the money paths
    here keep the status in step with the amounts, and a path that only
    flips the status (a manager closing a job out as paid) means the whole
    remainder was collected."""
    booking = booking or {}
    stored = r2(booking.get("amount_paid")) if booking.get("amount_paid") is not None else 0.0
    if booking.get("payment_status") in _LEGACY_RECEIVED:
        return max(stored, max(0.0, r2(total_of(booking) - wallet_applied_of(booking))))
    return stored


def status_for(total, wallet_applied, amount_paid) -> str:
    """pending (nothing paid, something due) · partially_paid (something
    paid, something still due) · paid (nothing due)."""
    due = r2(float(total or 0) - float(wallet_applied or 0) - float(amount_paid or 0))
    if due <= EPSILON:
        return PAID
    return PARTIALLY_PAID if float(amount_paid or 0) > EPSILON else PENDING


def amount_due(booking: dict) -> float:
    """What is still to be collected for this booking. Nothing on a booking
    whose money was already returned/owed back, or that was cancelled."""
    booking = booking or {}
    if booking.get("payment_status") in _TERMINAL or booking.get("status") == "cancelled" or booking.get("is_deleted"):
        return 0.0
    return max(0.0, r2(total_of(booking) - wallet_applied_of(booking) - amount_paid_of(booking)))


def money_view(booking: dict) -> dict:
    """{"total", "wallet_applied", "amount_paid", "amount_due",
    "payment_status"} — the one shape every screen and path reads."""
    booking = booking or {}
    total = total_of(booking)
    applied = wallet_applied_of(booking)
    paid = amount_paid_of(booking)
    stored = booking.get("payment_status")
    if stored in _TERMINAL:
        status = stored
        due = 0.0
    else:
        status = status_for(total, applied, paid)
        due = amount_due(booking)
    return {
        "total": total,
        "wallet_applied": applied,
        "amount_paid": paid,
        "amount_due": due,
        "payment_status": status,
        "wallet_due_carried": r2(booking.get("wallet_due_carried")),
    }


def owes(booking: dict) -> bool:
    return amount_due(booking) > EPSILON


def is_paid(booking: dict) -> bool:
    """Fully paid (nothing due) — refund states are not "paid"."""
    return money_view(booking)["payment_status"] == PAID


# -- what a cancellation returns -------------------------------------------


def refundable(booking: dict) -> float:
    """What cancelling this booking gives back to the wallet before any
    charge: everything received + the wallet credit it spent — minus a
    carried previous balance that was already settled (credited back to
    the wallet when it was paid: that money paid an old debt, not this
    booking). Nothing for a booking whose money already went back."""
    booking = booking or {}
    if booking.get("payment_status") in _TERMINAL or booking.get("cancel_money_settled"):
        return 0.0
    back = amount_paid_of(booking) + wallet_applied_of(booking)
    if booking.get("wallet_due_cleared"):
        back -= r2(booking.get("wallet_due_carried"))
    return max(0.0, r2(back))


def paid_by_customer(booking: dict) -> float:
    """What the customer paid for this booking — money received plus the
    wallet credit it spent, less a carried previous balance that was settled
    with it (that money paid an old debt). The ceiling for everything a
    manager may pay back for it (MONEY-2). A booking cancelled to the
    wallet: exactly what went back (`refunded_amount`, before any charge)."""
    booking = booking or {}
    if booking.get("refunded_to") == "wallet" and booking.get("refunded_amount") is not None:
        return max(0.0, r2(booking["refunded_amount"]))
    if booking.get("status") == "cancelled" and booking.get("amount_paid") is not None:
        # Its stored status says "refunded", which reads as "the whole total
        # was received" — the stored amount is what actually came in.
        paid = r2(booking["amount_paid"])
    else:
        paid = amount_paid_of(booking)
    paid += wallet_applied_of(booking)
    if booking.get("wallet_due_cleared"):
        paid -= r2(booking.get("wallet_due_carried"))
    return max(0.0, r2(paid))


# -- captain wallet --------------------------------------------------------


def captain_wallet_exempt(booking: dict) -> bool:
    """No captain is paid through the wallet for this booking: nobody did
    it as a captain, or a manager closed it out / logged it."""
    booking = booking or {}
    return not booking.get("captain_id") or booking.get("completed_by_role") == "manager"


def captain_cash_held(booking: dict) -> float:
    """Cash for this booking that its captain took from the customer."""
    booking = booking or {}
    if booking.get("captain_cash_collected") is not None:
        return r2(booking["captain_cash_collected"])
    # Legacy: cash settled at completion ("cash"), or collected afterwards
    # by the same captain through captain_collect_cash.
    if booking.get("wallet_settled_as") == "cash":
        return total_of(booking)
    if (
        booking.get("payment_method") == "cash"
        and booking.get("payment_status") in _LEGACY_RECEIVED
        and booking.get("cash_collected_by")
        and booking.get("cash_collected_by") == booking.get("captain_id")
    ):
        return total_of(booking)
    return 0.0


def captain_wallet_target(booking: dict) -> float:
    """Where this booking should leave its captain's wallet: his earning
    once the job is done, minus the cash he holds for the platform."""
    booking = booking or {}
    if captain_wallet_exempt(booking):
        return 0.0
    earned = r2(booking.get("captain_earning")) if booking.get("status") == "completed" else 0.0
    return r2(earned - captain_cash_held(booking))


def captain_wallet_posted(booking: dict) -> float:
    """What was already posted to the captain's wallet for this booking.
    Bookings settled by the old completion code (wallet_settled_as) carry
    no `captain_wallet_posted`: it is inferred from how they settled."""
    booking = booking or {}
    if booking.get("captain_wallet_posted") is not None:
        return r2(booking["captain_wallet_posted"])
    if not booking.get("wallet_settled") or not booking.get("wallet_settled_as") or captain_wallet_exempt(booking):
        return 0.0
    earning = r2(booking.get("captain_earning"))
    if booking.get("wallet_settled_as") == "cash":
        return r2(earning - total_of(booking))
    # "credit": the earning went in; a later doorstep cash collection by the
    # same captain debited the whole total.
    return r2(earning - captain_cash_held(booking))
