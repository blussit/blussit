from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.schemas.content_schema import BookingPolicyUpdateRequest
from app.services.audit_service import AuditService
from app.services.booking_policy_service import BookingPolicyService

router = APIRouter(prefix="/booking-policy", tags=["Booking Policy"])


@router.get("")
async def get_booking_policy(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — the booking form needs this to build the time picker and validate client-side."""
    return success(await BookingPolicyService(db).get_policy())


@router.put("", dependencies=[Depends(require_admin)])
async def set_booking_policy(
    payload: BookingPolicyUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await BookingPolicyService(db).set_policy(payload.model_dump(exclude_unset=True))
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_BOOKING_POLICY", "settings", None, payload.model_dump(exclude_unset=True))
    return success(result, "Booking policy updated")
