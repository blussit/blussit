from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.notification_controller import NotificationController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_manager_or_admin
from app.schemas.notification_schema import NotificationPreferencesUpdate, UniversalMessageRequest

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
async def list_notifications(unread_only: bool = False, pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).list_mine(current_user, pagination, unread_only)


@router.get("/preferences")
async def get_preferences(current_user: CurrentUser = Depends(require_manager_or_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The signed-in manager's / admin's own WhatsApp alert settings."""
    return await NotificationController(db).get_preferences(current_user)


@router.put("/preferences")
async def update_preferences(
    payload: NotificationPreferencesUpdate,
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Switch "WhatsApp me new bookings" on/off — own account; an admin may
    pass user_id for a manager. Audited."""
    return await NotificationController(db).update_preferences(current_user, payload)


@router.post("/universal-message")
async def send_universal_message(
    payload: UniversalMessageRequest,
    current_user: CurrentUser = Depends(require_manager_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Admin / manager writes a message to one customer; it goes out through
    the approved universal_message template (never free text outside the
    24-hour window). Manager: customers known to their center. Audited."""
    return await NotificationController(db).send_universal_message(current_user, payload)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).mark_read(current_user, notification_id)


@router.post("/read-all")
async def mark_all_read(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).mark_all_read(current_user)
