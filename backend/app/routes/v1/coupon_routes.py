from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.coupon_controller import CouponController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin, require_customer
from app.schemas.coupon_schema import CouponCreateRequest, CouponUpdateRequest, CouponValidateRequest

router = APIRouter(prefix="/coupons", tags=["Coupons"])


@router.get("", dependencies=[Depends(require_admin)])
async def list_coupons(active_only: bool = False, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CouponController(db).list(pagination, active_only)


@router.post("", dependencies=[Depends(require_admin)])
async def create_coupon(payload: CouponCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CouponController(db).create(current_user, payload)


@router.put("/{coupon_id}", dependencies=[Depends(require_admin)])
async def update_coupon(coupon_id: str, payload: CouponUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CouponController(db).update(current_user, coupon_id, payload)


@router.delete("/{coupon_id}", dependencies=[Depends(require_admin)])
async def delete_coupon(coupon_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CouponController(db).delete(current_user, coupon_id)


@router.post("/validate", dependencies=[Depends(require_customer)])
async def validate_coupon(payload: CouponValidateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await CouponController(db).validate(current_user, payload)
