"""Coupon bundles (admin only) — mounted under /coupons/bundles by
coupon_routes.py. Every route here requires an admin."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams, get_db, require_admin
from app.core.responses import paginated, success
from app.schemas.coupon_bundle_schema import BundleAddCodesRequest, BundleCreateRequest, BundleUpdateRequest, CodeActiveRequest
from app.services.coupon_bundle_service import CouponBundleService
from app.utils.timezone import IST

router = APIRouter(prefix="/bundles", tags=["Coupon Bundles"])


def _ist(value: Optional[datetime]) -> Optional[datetime]:
    """A zone-less filter time is IST (what the admin picked)."""
    return value.replace(tzinfo=IST) if value is not None and value.tzinfo is None else value


class CodeFilters:
    def __init__(
        self,
        status: Optional[str] = Query(None),
        search: Optional[str] = Query(None, max_length=40),
        sort_by: str = Query("added", pattern="^(added|used|code|uses)$"),
        sort_order: int = Query(-1, ge=-1, le=1),
        added_from: Optional[datetime] = Query(None),
        added_to: Optional[datetime] = Query(None),
        used_from: Optional[datetime] = Query(None),
        used_to: Optional[datetime] = Query(None),
    ):
        self.kwargs = dict(
            status=status or None, search=search, sort_by=sort_by, sort_order=-1 if sort_order != 1 else 1,
            added_from=_ist(added_from), added_to=_ist(added_to), used_from=_ist(used_from), used_to=_ist(used_to),
        )


@router.get("/sample.csv")
async def sample_csv(admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return Response(
        CouponBundleService.sample_csv(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="coupon-bundle-sample.csv"', "Cache-Control": "no-store"},
    )


@router.post("/preview")
async def preview(file: UploadFile = File(...), admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = CouponBundleService(db)
    return success(await service.preview(await service.read_upload(file)), "File read")


@router.post("")
async def create_bundle(payload: BundleCreateRequest, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).create_bundle(payload, admin.id, admin.role), "Bundle created")


@router.get("")
async def list_bundles(
    search: Optional[str] = Query(None, max_length=80), pagination: PaginationParams = Depends(),
    admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    items, total = await CouponBundleService(db).list_bundles(pagination.page, pagination.page_size, search)
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/{bundle_id}")
async def get_bundle(bundle_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).get_bundle(bundle_id))


@router.put("/{bundle_id}")
async def update_bundle(bundle_id: str, payload: BundleUpdateRequest, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).update_bundle(bundle_id, payload, admin.id, admin.role), "Bundle updated")


@router.delete("/{bundle_id}")
async def delete_bundle(bundle_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await CouponBundleService(db).delete_bundle(bundle_id, admin.id, admin.role)
    note = "Bundle deleted" if result["mode"] == "deleted" else "Bundle switched off and hidden — some codes were already used, so its history is kept"
    return success(result, note)


@router.post("/{bundle_id}/codes")
async def add_codes(bundle_id: str, payload: BundleAddCodesRequest, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).add_codes(bundle_id, payload.codes, admin.id, admin.role), "Codes added")


@router.get("/{bundle_id}/codes")
async def list_codes(
    bundle_id: str, filters: CodeFilters = Depends(), pagination: PaginationParams = Depends(),
    admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    items, total = await CouponBundleService(db).list_codes(bundle_id, pagination.page, pagination.page_size, **filters.kwargs)
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/{bundle_id}/export.csv")
async def export_codes(bundle_id: str, filters: CodeFilters = Depends(), admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    body = await CouponBundleService(db).export_csv(bundle_id, admin.id, admin.role, **filters.kwargs)
    return StreamingResponse(
        body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="coupon-bundle.csv"', "Cache-Control": "no-store"},
    )


@router.put("/{bundle_id}/codes/{code_id}")
async def set_code_active(bundle_id: str, code_id: str, payload: CodeActiveRequest, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).set_code_active(bundle_id, code_id, payload.is_active, admin.id, admin.role), "Code updated")


@router.get("/{bundle_id}/codes/{code_id}/uses")
async def code_uses(bundle_id: str, code_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CouponBundleService(db).code_uses(bundle_id, code_id))
