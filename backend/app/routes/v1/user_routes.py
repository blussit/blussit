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
    period: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    center_id: Optional[str] = None,
    city: Optional[str] = None,
    pincode: Optional[str] = None,
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Customers also carry their home center / city / pincode (filter with
    `center_id`, `city`, `pincode`; `sort_by` may be center, city, pincode,
    bookings_count, last_booking_at, full_name, created_at).

    `period`/`start`/`end` match the admin KPI dashboard's "New
    customers" tile exactly (see KpiService.resolve_period) — filters on
    created_at, the same field that tile counts."""
    return await UserController(db).list_users(role, period, start, end, pagination, center_id, city, pincode)


@router.get("/location-facets", dependencies=[Depends(require_admin)])
async def location_facets(db: AsyncIOMotorDatabase = Depends(get_db)):
    from app.core.responses import success
    from app.services.customer_location_service import location_facets as facets

    return success(await facets(db))


@router.put("/{user_id}/home-center", dependencies=[Depends(require_admin)])
async def set_home_center(
    user_id: str, payload: dict, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Admin override of a customer's home center ("manual"); center_id null
    clears the override and re-derives from addresses/bookings."""
    from app.core.responses import success
    from app.services.audit_service import AuditService
    from app.services.customer_location_service import set_manual_location

    result = await set_manual_location(db, user_id, (payload or {}).get("center_id"))
    await AuditService(db).log_action(current_user.id, current_user.role, "SET_CUSTOMER_HOME_CENTER", "users", user_id, {"center_id": (payload or {}).get("center_id")})
    return success(result, "Home center updated")


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
