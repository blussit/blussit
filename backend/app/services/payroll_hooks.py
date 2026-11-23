"""The ONE transactional hook every booking money / status change calls
(§7.13 H3): captain completion, manager mark-done, a logged job, admin edit
(status, captain, price), a manager discount, an on-site add-on, delete and
restore. It runs inside the caller's transaction (`session=`), so the booking's
change and the money it implies commit together:

  1. PayrollService.sync_commission — the captain's commission posting;
  2. FranchiseService.sync_share    — the franchise partner's revenue share.

Both are idempotent delta syncs keyed by a guarded revision on the booking, so
calling the hook again, or from two paths, posts nothing twice."""
from __future__ import annotations

import logging

from bson import ObjectId

logger = logging.getLogger(__name__)

try:  # backend B's module — the hook must work before it lands
    from app.services.franchise_service import FranchiseService
except ImportError:  # pragma: no cover
    FranchiseService = None  # type: ignore[assignment]


async def after_booking_money_change(db, booking_id_or_doc, session=None) -> None:
    """Sync commission, then the franchise share, for one booking."""
    from app.services.payroll_service import PayrollService

    oid = booking_id_or_doc.get("_id") if isinstance(booking_id_or_doc, dict) else (
        ObjectId(str(booking_id_or_doc)) if ObjectId.is_valid(str(booking_id_or_doc or "")) else None
    )
    if oid is None:
        return
    await PayrollService(db).sync_commission(oid, session=session)
    if FranchiseService is not None:
        booking = await db.bookings.find_one({"_id": oid}, session=session)
        if booking:
            await FranchiseService(db).sync_share(booking, session=session)


async def after_bookings_money_change(db, booking_ids, session=None) -> None:
    for booking_id in booking_ids or []:
        await after_booking_money_change(db, booking_id, session=session)
