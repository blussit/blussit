"""
Muting the manager's per-booking "New booking — needs a captain" alert for
bookings the manager scheduled himself (society visit days: one schedule
can create a dozen bookings, each already given a captain a moment later).
The resident's own confirmation is untouched. Scoped with a ContextVar, so
it only ever affects the code running inside `muted_manager_new_booking_alerts()`.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_muted: ContextVar[bool] = ContextVar("muted_manager_new_booking_alerts", default=False)


def manager_new_booking_alerts_muted() -> bool:
    return _muted.get()


@contextmanager
def muted_manager_new_booking_alerts():
    token = _muted.set(True)
    try:
        yield
    finally:
        _muted.reset(token)
