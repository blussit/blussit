"""
Admin-only booking tools (founder 2026-10-28).

  PATCH /admin/bookings/{id}          edit anything on any booking
  GET   /admin/bookings/{id}/history  the booking's money-change history

The whole router is behind require_admin — a manager, captain or customer
gets 403 before any booking is looked up.
"""
from fastapi import APIRouter, Depends, Path
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.schemas.booking_adjust_schema import AdminBookingEditRequest

router = APIRouter(prefix="/admin/bookings", tags=["Admin Bookings"], dependencies=[Depends(require_admin)])


@router.patch("/{booking_id}")
async def admin_edit_booking(
    payload: AdminBookingEditRequest,
    booking_id: str = Path(pattern=r"^[0-9a-fA-F]{24}$"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Edit every part of a booking, in any status: customer, address and map
    pin, services / car / add-ons / coupon, date and slot, captain, discount,
    tip, payment corrections, status. `reason` is required; `dry_run=true`
    previews the money impact (what the customer owes, what goes back to the
    wallet, the captain's wallet change) without saving; `expected_version`
    (from the booking read) makes a stale edit a 409; `idempotency_key` makes
    a retry harmless. Payment rows are never changed — a correction is an
    appended adjustment. Every edit is recorded, before / after, with who
    and why, and the center's manager is told."""
    from app.services.booking_admin_edit_service import BookingAdminEditService

    result = await BookingAdminEditService(db).edit(booking_id, payload, {"id": current_user.id, "role": current_user.role})
    if result.get("dry_run"):
        return success(result, "Preview — nothing saved")
    return success(result, "Booking updated")


@router.get("/{booking_id}/history")
async def admin_booking_history(
    booking_id: str = Path(pattern=r"^[0-9a-fA-F]{24}$"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from app.services.booking_admin_edit_service import BookingAdminEditService

    return success(await BookingAdminEditService(db).history(booking_id))
