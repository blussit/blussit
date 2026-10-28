"""
Festival / broadcast campaigns (admin only). The heavy lifting is in
services/whatsapp_campaign_service.py; "send" only validates and queues - the
worker drains. Every create / send / cancel / test is audited.
"""
from datetime import date

from fastapi import APIRouter, Depends, File, Query, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.upload_controller import UploadController
from app.core.dependencies import CurrentUser, get_db, require_admin
from app.core.exceptions import BadRequestException
from app.core.responses import success
from app.core.storage import record_photo_upload, save_photo_upload
from app.schemas.whatsapp_campaign_schema import (
    AudienceFilter,
    CampaignCreate,
    CampaignUpdate,
    LimitsUpdate,
    SelectionBody,
    SendBody,
)
from app.services import whatsapp_campaign_service as svc
from app.services.audit_service import AuditService
from app.services.whatsapp_campaign_template import MAX_MESSAGE_CHARS, sanitize_message

router = APIRouter(prefix="/whatsapp/campaigns", tags=["WhatsApp Campaigns"])

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # WhatsApp's own limit for a header image
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def _audit(db):
    return AuditService(db)


def _filter(
    search: str = Query("", max_length=100), center_id: str | None = None, city: str | None = None,
    last_booking_from: date | None = None, last_booking_to: date | None = None, no_booking: bool | None = None,
    has_active_plan: bool | None = None, bookings_min: int | None = Query(None, ge=0), bookings_max: int | None = Query(None, ge=0),
) -> AudienceFilter:
    return AudienceFilter(
        search=search, center_id=center_id, city=city, last_booking_from=last_booking_from, last_booking_to=last_booking_to,
        no_booking=no_booking, has_active_plan=has_active_plan, bookings_min=bookings_min, bookings_max=bookings_max,
    )


@router.get("")
async def list_campaigns(page: int = 1, page_size: int = 20, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await svc.list_campaigns(db, page, page_size))


@router.get("/limits")
async def get_limits(admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success({**await svc.get_limits(db), "max_message_chars": MAX_MESSAGE_CHARS, "template": await svc.template_state(db)})


@router.put("/limits")
async def update_limits(body: LimitsUpdate, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    limits = await svc.update_limits(db, body.whatsapp_marketing_cost_paise, admin.id)
    await _audit(db).log_action(admin.id, admin.role, "UPDATE_CAMPAIGN_SETTINGS", "whatsapp", None, {"cost_paise": body.whatsapp_marketing_cost_paise})
    return success(limits, "Saved")


@router.get("/audience")
async def audience(
    page: int = 1, page_size: int = 25, after: str | None = None, f: AudienceFilter = Depends(_filter),
    admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await svc.list_audience(db, f, None, page, page_size, after))


@router.post("/audience/selection")
async def audience_page_with_selection(
    body: SelectionBody, page: int = 1, page_size: int = 25, after: str | None = None,
    admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """One page of the filtered audience with each row's `selected` flag
    resolved against the selection, plus the selected / matching totals."""
    sel = body.selection
    data = await svc.list_audience(db, sel.filter, sel, page, page_size, after)
    data["selected_count"] = await svc.count_selection(db, sel)
    return success(data)


@router.post("/audience/count")
async def audience_count(body: SelectionBody, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    sel = body.selection
    matching = await svc.list_audience(db, sel.filter, sel, 1, 1)
    return success({"selected": await svc.count_selection(db, sel), "matching_total": matching["total"]})


@router.post("/image")
async def upload_image(file: UploadFile = File(...), admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The festival picture: JPEG or PNG, up to 5 MB, stored on the public
    image storage so Meta can fetch it."""
    if (file.content_type or "") not in ("image/jpeg", "image/png"):
        raise BadRequestException("Only JPG or PNG images are accepted")
    size = 0
    head = b""
    while chunk := await file.read(1024 * 1024):
        if not head:
            head = chunk[:16]
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise BadRequestException("The image is too large - keep it under 5 MB")
    if size == 0:
        raise BadRequestException("The uploaded file is empty")
    if not head.startswith(IMAGE_MAGIC):
        raise BadRequestException("That file is not a real JPG or PNG image")
    await file.seek(0)
    await UploadController(db)._count_upload(admin.id)
    url = await save_photo_upload(file)
    await record_photo_upload(db, url, admin.id, admin.role, None)
    return success({"url": url}, "Image uploaded")


@router.post("")
async def create_campaign(body: CampaignCreate, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    c = await svc.create_draft(db, admin, body.name, body.message, body.image_url)
    await _audit(db).log_action(admin.id, admin.role, "CREATE_CAMPAIGN", "whatsapp", str(c["_id"]), {"name": c["name"]})
    return success(svc._public(c), "Campaign saved")


@router.post("/sanitize")
async def sanitize(body: dict, admin: CurrentUser = Depends(require_admin)):
    """What the customer will actually receive for this text."""
    text = sanitize_message(str((body or {}).get("message") or ""))
    return success({"message": text, "length": len(text), "max": MAX_MESSAGE_CHARS})


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await svc.progress(db, campaign_id))


@router.put("/{campaign_id}")
async def update_campaign(campaign_id: str, body: CampaignUpdate, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    c = await svc.update_draft(db, campaign_id, body.model_dump(exclude_none=True))
    await _audit(db).log_action(admin.id, admin.role, "UPDATE_CAMPAIGN", "whatsapp", campaign_id, {"fields": sorted(body.model_dump(exclude_none=True))})
    return success(svc._public(c), "Campaign saved")


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    await svc.delete_draft(db, campaign_id)
    await _audit(db).log_action(admin.id, admin.role, "DELETE_CAMPAIGN", "whatsapp", campaign_id, {})
    return success(None, "Draft deleted")


@router.post("/{campaign_id}/preview")
async def preview(campaign_id: str, body: SelectionBody, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await svc.quote(db, await svc.get_campaign(db, campaign_id), body.selection))


@router.post("/{campaign_id}/test")
async def test_send(campaign_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await svc.send_test(db, admin, campaign_id)
    await _audit(db).log_action(admin.id, admin.role, "TEST_CAMPAIGN", "whatsapp", campaign_id, {})
    return success(result, "Test message sent to your number")


@router.post("/{campaign_id}/send")
async def send(campaign_id: str, body: SendBody, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await svc.send_campaign(db, admin, campaign_id, body.selection, body.confirm_count)
    if not result.get("already_started"):
        await _audit(db).log_action(
            admin.id, admin.role, "SEND_CAMPAIGN", "whatsapp", campaign_id,
            {"recipients": result["total_recipients"], "cost_estimate_paise": result["cost_estimate_paise"], "selection": result["selection_summary"]},
        )
    return success(result, "Campaign queued - sending has started")


@router.post("/{campaign_id}/cancel")
async def cancel(campaign_id: str, admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await svc.cancel_campaign(db, admin, campaign_id)
    if not result.get("already_finished"):
        await _audit(db).log_action(admin.id, admin.role, "CANCEL_CAMPAIGN", "whatsapp", campaign_id, {"counts": result["counts"]})
    return success(result, "Campaign cancelled - unsent messages will not go out")


@router.get("/{campaign_id}/recipients")
async def recipients(
    campaign_id: str, status: str | None = None, page: int = 1, page_size: int = 50,
    admin: CurrentUser = Depends(require_admin), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await svc.list_recipients(db, campaign_id, status, page, page_size))
