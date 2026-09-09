from datetime import datetime
from typing import Optional

from app.models.base import BusinessRecordBase


class CaptainLocationModel(BusinessRecordBase):
    """One GPS breadcrumb. Written at every real capture moment — the 25s
    on-job ping, each workflow transition (heading / arrival / before-photo /
    after-photo) and attendance check-in/out — so a manager can audit where a
    captain actually was, not just the latest overwritten position
    (users.last_known_location stays the "current" pointer; this is the
    trail). Rows expire automatically after 30 days via a TTL index on `at`
    (see create_indexes) — this is a short-lived operational audit trail,
    not a permanent movement archive."""

    captain_id: str
    booking_id: Optional[str] = None
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None
    # ping | heading | arrival | before_photo | after_photo | check_in | check_out
    source: str = "ping"
    at: datetime
