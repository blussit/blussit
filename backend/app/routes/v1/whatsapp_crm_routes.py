"""
WhatsApp CRM panel routes (admin-only). All Meta credentials stay
server-side; media is proxied, never linked directly. Assignment/status/
tag changes are audited.
"""
from fastapi import APIRouter, Depends, File, Response, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.exceptions import BadRequestException
from app.core.responses import success
from app.services.audit_service import AuditService
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
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await WhatsAppCrmService(db).list_conversations(filter, search, current_user.id))


@router.get("/conversations/{wa_id}/messages")
async def get_thread(wa_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).get_thread(wa_id))


@router.post("/conversations/{wa_id}/read")
async def mark_read(wa_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    await WhatsAppCrmService(db).mark_read(wa_id)
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
async def contacts(search: str = "", db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).contacts(search))


@router.get("/agents")
async def agents(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).assignable_agents())


@router.get("/badge")
async def badge(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).unread_badge())


@router.get("/tags")
async def default_tags():
    return success({"tags": DEFAULT_TAGS})


@router.get("/analytics")
async def analytics(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).analytics(days))


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
async def list_templates(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).list_local_templates())


@router.post("/templates/sync")
async def sync_templates(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await WhatsAppCrmService(db).sync_templates())


@router.post("/templates")
async def create_template(payload: CreateTemplateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await WhatsAppCrmService(db).create_template(payload.name, payload.category, payload.language, payload.body, payload.button_text, payload.button_url)
    await AuditService(db).log_action(current_user.id, current_user.role, "WHATSAPP_CREATE_TEMPLATE", "whatsapp", payload.name, {"category": payload.category})
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
