"""
WhatsApp CRM panel routes (admin-only). All Meta credentials stay
server-side; media is proxied, never linked directly. Assignment/status/
tag changes are audited.
"""
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.exceptions import BadRequestException
from app.core.responses import success
from app.services import report_cache
from app.services.audit_service import AuditService
from app.schemas.notification_schema import WhatsAppSettingsUpdate
from app.services.whatsapp_crm_service import DEFAULT_TAGS, WhatsAppCrmService, bootstrap_blussit_templates

router = APIRouter(prefix="/whatsapp/crm", tags=["WhatsApp CRM"], dependencies=[Depends(require_admin)])

MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # WhatsApp media cap for most types
ALLOWED_UPLOAD_TYPES = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "video/mp4": "video", "video/3gpp": "video",
    "audio/aac": "audio", "audio/mpeg": "audio", "audio/ogg": "audio", "audio/mp4": "audio",
}


class SendTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class SendTemplateRequest(BaseModel):
    template_name: str
    params: list[str] = []


class StartConversationRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=15)
    template_name: str
    params: list[str] = []


class SendMediaRequest(BaseModel):
    media_type: str
    media_id: str
    caption: str = ""
    filename: str = ""


class AssignRequest(BaseModel):
    user_id: str | None = None


class StatusRequest(BaseModel):
    status: str


class TagsRequest(BaseModel):
    tags: list[str]


class BotPausedRequest(BaseModel):
    paused: bool


class CreateTemplateRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    category: str
    language: str = "en_US"
    body: str = Field(min_length=10, max_length=1024)
    button_text: str | None = None
    button_url: str | None = None


@router.get("/conversations")
async def list_conversations(
    filter: str = "all", search: str = "",
    limit: int = Query(50, ge=1, le=100),
    before: str | None = Query(None, description="last_message_at of the last row already shown — returns the next, older page"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await WhatsAppCrmService(db).list_conversations(filter, search, current_user.id, limit=limit, before=before))


@router.get("/conversations/{wa_id}/messages")
async def get_thread(wa_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).get_thread(wa_id))


@router.post("/conversations/{wa_id}/read")
async def mark_read(wa_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    await WhatsAppCrmService(db).mark_read(wa_id)
    report_cache.invalidate("wa_badge")
    return success({"read": True})


@router.post("/conversations/{wa_id}/send")
async def send_text(wa_id: str, payload: SendTextRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).send_text(wa_id, payload.text, current_user.id))


@router.post("/conversations/{wa_id}/send-template")
async def send_template(wa_id: str, payload: SendTemplateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).send_template_message(wa_id, payload.template_name, payload.params, current_user.id))


@router.post("/conversations/{wa_id}/send-media")
async def send_media(wa_id: str, payload: SendMediaRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).send_media_message(wa_id, payload.media_type, payload.media_id, payload.caption, payload.filename, current_user.id))


@router.post("/conversations/start")
async def start_conversation(payload: StartConversationRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).send_template_message(payload.phone, payload.template_name, payload.params, current_user.id)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_START_CONVERSATION", "whatsapp", result.get("wa_id", payload.phone), {"template": payload.template_name})
    return success(result)


@router.post("/conversations/{wa_id}/assign")
async def assign(wa_id: str, payload: AssignRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).assign(wa_id, payload.user_id)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_ASSIGN_CONVERSATION", "whatsapp", wa_id, result)
    return success(result)


@router.post("/conversations/{wa_id}/status")
async def set_status(wa_id: str, payload: StatusRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).set_status(wa_id, payload.status)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_SET_STATUS", "whatsapp", wa_id, result)
    return success(result)


@router.post("/conversations/{wa_id}/tags")
async def set_tags(wa_id: str, payload: TagsRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).set_tags(wa_id, payload.tags)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_SET_TAGS", "whatsapp", wa_id, result)
    return success(result)


@router.post("/conversations/{wa_id}/bot")
async def set_bot_paused(wa_id: str, payload: BotPausedRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).set_bot_paused(wa_id, payload.paused)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_BOT_TOGGLE", "whatsapp", wa_id, result)
    return success(result)


@router.get("/contacts/{wa_id}")
async def contact_profile(wa_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).contact_profile(wa_id))


@router.get("/contacts")
async def contacts(
    search: str = "", limit: int = Query(50, ge=1, le=100), before: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await WhatsAppCrmService(db).contacts(search, limit=limit, before=before))


@router.get("/agents")
async def agents(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).assignable_agents())


@router.get("/badge")
async def badge(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Polled by every open admin console tab — one shared count per
    instance every 15 s instead of one per tab per poll."""
    return success(await report_cache.cached(("wa_badge",), 15, WhatsAppCrmService(db).unread_badge))


@router.get("/tags")
async def default_tags():
    return success({"tags": DEFAULT_TAGS})


@router.get("/analytics")
async def analytics(days: int = Query(30, ge=1, le=365), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await report_cache.cached(("wa_analytics", days), 60, lambda: WhatsAppCrmService(db).analytics(days)))


@router.get("/delivery-health")
async def delivery_health(days: int = Query(7, ge=1, le=90), fresh: bool = False, db: AsyncIOMotorDatabase = Depends(get_db)):
    """What did NOT reach people on WhatsApp: queued sends by status
    (incl. "undelivered" — accepted by Meta, then reported failed),
    failures by reason (no_template, transport, rejected, opted_out, auth,
    no_recipient, business_number, undelivered…), free text refused
    outside the 24-hour window, latest failures, and config_warnings
    (managers who can't get alerts, unapproved/unknown templates, Meta
    refusing our credentials, templates re-filed as MARKETING).

    Cached 15 s per instance; `?fresh=1` (the card's Refresh) recomputes."""
    from app.services.notification_service import NotificationService

    if fresh:
        report_cache.invalidate("wa_delivery_health")
    return success(await report_cache.cached(("wa_delivery_health", days), 15, lambda: NotificationService(db).delivery_health(days)))


@router.get("/settings")
async def get_settings(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Admin WhatsApp settings: google_review_url (the review template's
    button and the review-request sweep's on/off)."""
    return success(await WhatsAppCrmService(db).get_settings())


@router.put("/settings")
async def update_settings(payload: WhatsAppSettingsUpdate, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    result = await WhatsAppCrmService(db).update_settings(changes, actor_id=current_user.id)
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_WHATSAPP_SETTINGS", "whatsapp", "settings", {"fields": sorted(changes)})
    return success(result, message="WhatsApp settings saved")


# ---- media -----------------------------------------------------------------

@router.post("/media")
async def upload_media(file: UploadFile = File(...), db: AsyncIOMotorDatabase = Depends(get_db)):
    mime = file.content_type or "application/octet-stream"
    media_type = ALLOWED_UPLOAD_TYPES.get(mime)
    if not media_type:
        raise BadRequestException(f"Unsupported file type '{mime}' for WhatsApp")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise BadRequestException("File exceeds the 16MB WhatsApp media limit")
    media_id = await WhatsAppCrmService(db).wa.upload_media(content, mime, file.filename or "upload")
    if not media_id:
        raise BadRequestException("Upload to WhatsApp failed — try again")
    return success({"media_id": media_id, "media_type": media_type, "filename": file.filename, "size": len(content)})


@router.get("/media/{media_id}")
async def fetch_media(media_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).wa.fetch_media(media_id)
    if not result:
        raise BadRequestException("Media unavailable (expired or unsupported provider)")
    content, mime = result
    return Response(content=content, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


# ---- templates -------------------------------------------------------------

@router.get("/templates")
async def list_templates(sendable: bool = False, db: AsyncIOMotorDatabase = Depends(get_db)):
    """sendable=true: only what the inbox's agent picker can really send
    (see WhatsAppCrmService.agent_sendable_templates)."""
    service = WhatsAppCrmService(db)
    return success(await (service.agent_sendable_templates() if sendable else service.list_local_templates()))


@router.post("/templates/sync")
async def sync_templates(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).sync_templates()
    # ADM-12: a manual sync can flip which templates the app uses — audited.
    summary = {k: result.get(k) for k in ("synced", "count", "updated", "added") if isinstance(result, dict) and k in result}
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_SYNC_TEMPLATES", "whatsapp", "all", summary)
    return success(result)


@router.post("/templates")
async def create_template(payload: CreateTemplateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).create_template(payload.name, payload.category, payload.language, payload.body, payload.button_text, payload.button_url)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_CREATE_TEMPLATE", "whatsapp", payload.name, {"category": payload.category})
    return success(result, message="Template submitted to WhatsApp for review")


@router.get("/templates/catalogue")
async def template_catalogue(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Every template the app uses today: copy, examples, button, which
    events use it, and its status on Meta — what Submit would send."""
    return success(await WhatsAppCrmService(db).template_catalogue())


@router.post("/templates/catalogue/{key}/submit")
async def submit_catalogue_template(key: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Submit one catalogue template to Meta (a rejected one as its next
    version name). Audited."""
    result = await WhatsAppCrmService(db).submit_catalogue_template(key)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_CREATE_TEMPLATE", "whatsapp", result.get("name"), {"key": key, "status": result.get("status")})
    return success(result, message="Template submitted to WhatsApp for review")


@router.post("/templates/bootstrap")
async def bootstrap_templates(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    results = await bootstrap_blussit_templates(db)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_BOOTSTRAP_TEMPLATES", "whatsapp", "all", {"count": len(results)})
    return success(results)


@router.patch("/templates/{name}/disabled")
async def set_template_disabled(name: str, payload: BotPausedRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).set_template_disabled(name, payload.paused)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_TEMPLATE_TOGGLE", "whatsapp", name, result)
    return success(result)
