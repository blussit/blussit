from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.notification_controller import NotificationController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
async def list_notifications(unread_only: bool = False, pagination: PaginationParams = Depends(), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).list_mine(current_user, pagination, unread_only)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).mark_read(current_user, notification_id)


@router.post("/read-all")
async def mark_all_read(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await NotificationController(db).mark_all_read(current_user)
