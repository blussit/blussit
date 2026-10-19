"""
Holidays (see HolidayService):

- admin: list / create / edit / end now / preview — every write audited
  (HOLIDAY_CREATE / HOLIDAY_UPDATE / HOLIDAY_END);
- public (no login): the holidays whose website popup is showing now;
- customer: the same for the center that serves them.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams, get_db, require_admin, require_customer
from app.core.responses import paginated, success
from app.schemas.holiday_schema import HolidayCreateRequest, HolidayUpdateRequest
from app.services.holiday_service import HolidayService

admin_router = APIRouter(prefix="/admin/holidays", tags=["Holidays"])
public_router = APIRouter(prefix="/public/holidays", tags=["Holidays"])
customer_router = APIRouter(prefix="/holidays", tags=["Holidays"])

# Visitors' browsers may reuse the answer for a minute; a change reaches
# everyone within ~90 s (this + the API's own 30 s holiday cache).
PUBLIC_HOLIDAYS_CACHE = "public, max-age=60"


@admin_router.get("")
async def list_holidays(
    status: Literal["upcoming", "active", "past", "all"] = "upcoming",
    pagination: PaginationParams = Depends(),
    _: CurrentUser = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    items, total = await HolidayService(db).list(status, pagination.page, pagination.page_size)
    return paginated(items, pagination.page, pagination.page_size, total)


@admin_router.post("/preview")
async def preview_holiday(
    payload: HolidayCreateRequest,
    holiday_id: str | None = Query(None, description="Preview an edit of this holiday"),
    _: CurrentUser = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Validation + labels + the popup + affected bookings — nothing written."""
    return success(await HolidayService(db).preview(payload, holiday_id=holiday_id))


@admin_router.post("")
async def create_holiday(payload: HolidayCreateRequest, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await HolidayService(db).create(payload, current_user.id, current_user.role)
    return success(result, "Holiday saved")


@admin_router.patch("/{holiday_id}")
async def update_holiday(
    holiday_id: str, payload: HolidayUpdateRequest, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await HolidayService(db).update(holiday_id, payload, current_user.id, current_user.role)
    return success(result, "Holiday updated")


@admin_router.post("/{holiday_id}/end")
async def end_holiday(holiday_id: str, current_user: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await HolidayService(db).end(holiday_id, current_user.id, current_user.role)
    return success(result, "Holiday ended — booking is open again")


@public_router.get("")
async def public_holidays(center_id: str | None = Query(None, max_length=64), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Holidays whose popup is showing now (notice_from ≤ now < end_at).
    Without center_id: the all-centers ones; with it, that center's too."""
    data = await HolidayService(db).public_list(center_id)
    return JSONResponse(content=success(data), headers={"Cache-Control": PUBLIC_HOLIDAYS_CACHE})


@customer_router.get("/mine")
async def my_holidays(current_user: CurrentUser = Depends(require_customer), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The public list for the center that serves this customer (their
    default address, else their last booking)."""
    return success(await HolidayService(db).for_customer(current_user.id))
