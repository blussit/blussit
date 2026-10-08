from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.schemas.charge_schema import BookingPolicyWithChargesUpdateRequest
from app.services.audit_service import AuditService
from app.services.booking_policy_service import BookingPolicyService

router = APIRouter(prefix="/booking-policy", tags=["Booking Policy"])


@router.get("")
async def get_booking_policy(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — the booking form needs this to build the time picker and
    validate client-side, and the cancellation-policy page reads the
    late-cancellation charge amounts (cancellation_fee_*) from it. The
    customer rules enforced in code ride along read-only (spec 1.2/1.3):
    free_cancel_hours, customer_edit_lock_minutes, plan_wash_forfeit_minutes."""
    from app.services.booking_service import CUSTOMER_CANCEL_LOCK_HOURS, CUSTOMER_EDIT_LOCK_MINUTES, PLAN_WASH_FORFEIT_MINUTES

    policy = await BookingPolicyService(db).get_policy()
    policy.update({
        "free_cancel_hours": CUSTOMER_CANCEL_LOCK_HOURS,
        "customer_edit_lock_minutes": CUSTOMER_EDIT_LOCK_MINUTES,
        "plan_wash_forfeit_minutes": PLAN_WASH_FORFEIT_MINUTES,
    })
    return success(policy)


@router.put("", dependencies=[Depends(require_admin)])
async def set_booking_policy(
    payload: BookingPolicyWithChargesUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    service = BookingPolicyService(db)
    before = await service.get_policy()
    result = await service.set_policy(dict(changes), updated_by=current_user.id)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_BOOKING_POLICY", "settings", None,
        {**changes, "before": {k: before.get(k) for k in changes}, "after": {k: result.get(k) for k in changes}},
    )
    return success(result, "Booking policy updated")
