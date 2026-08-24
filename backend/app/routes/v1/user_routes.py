from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.user_controller import UserController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin
from app.schemas.user_schema import AdminUserUpdateRequest, UserUpdateRequest

router = APIRouter(prefix="/users", tags=["Users"])


@router.put("/me")
async def update_my_profile(
    payload: UserUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await UserController(db).update_my_profile(current_user, payload)


@router.get("", dependencies=[Depends(require_admin)])
async def list_users(
    role: Optional[str] = None,
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await UserController(db).list_users(role, pagination)


@router.get("/{user_id}", dependencies=[Depends(require_admin)])
async def get_user(user_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserController(db).get_user(user_id)


@router.put("/{user_id}", dependencies=[Depends(require_admin)])
async def admin_update_user(
    user_id: str,
    payload: AdminUserUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await UserController(db).admin_update_user(current_user, user_id, payload)


@router.post("/{user_id}/suspend", dependencies=[Depends(require_admin)])
async def suspend_user(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await UserController(db).deactivate_user(current_user, user_id)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await UserController(db).delete_user(current_user, user_id)
