from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin
from app.core.responses import paginated, success
from app.schemas.content_schema import (
    ContactMessageCreateRequest,
    ContactMessageStatusUpdateRequest,
    FaqCreateRequest,
    FaqUpdateRequest,
    SettingUpsertRequest,
    TestimonialCreateRequest,
    TestimonialUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.content_service import ContactMessageService, FaqService, PublicStatsService, SettingService, TestimonialService

faq_router = APIRouter(prefix="/faqs", tags=["FAQs"])
testimonial_router = APIRouter(prefix="/testimonials", tags=["Testimonials"])
settings_router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=[Depends(require_admin)])
public_router = APIRouter(prefix="/public", tags=["Public Content"])
contact_router = APIRouter(prefix="/contact", tags=["Contact"])


@faq_router.get("")
async def list_faqs(active_only: bool = True, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FaqService(db).list_all(active_only))


@faq_router.post("", dependencies=[Depends(require_admin)])
async def create_faq(payload: FaqCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await FaqService(db).create(payload)
    await AuditService(db).log_action(current_user.id, current_user.role, "CREATE_FAQ", "faqs", result["id"])
    return success(result, "FAQ created successfully")


@faq_router.put("/{faq_id}", dependencies=[Depends(require_admin)])
async def update_faq(faq_id: str, payload: FaqUpdateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await FaqService(db).update(faq_id, payload), "FAQ updated successfully")


@faq_router.delete("/{faq_id}", dependencies=[Depends(require_admin)])
async def delete_faq(faq_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    await FaqService(db).delete(faq_id)
    return success(None, "FAQ deleted successfully")


@testimonial_router.get("")
async def list_testimonials(featured_only: bool = True, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await TestimonialService(db).list_all(featured_only))


@testimonial_router.post("", dependencies=[Depends(require_admin)])
async def create_testimonial(payload: TestimonialCreateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await TestimonialService(db).create(payload), "Testimonial created successfully")


@testimonial_router.put("/{testimonial_id}", dependencies=[Depends(require_admin)])
async def update_testimonial(testimonial_id: str, payload: TestimonialUpdateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await TestimonialService(db).update(testimonial_id, payload), "Testimonial updated successfully")


@testimonial_router.delete("/{testimonial_id}", dependencies=[Depends(require_admin)])
async def delete_testimonial(testimonial_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    await TestimonialService(db).delete(testimonial_id)
    return success(None, "Testimonial deleted successfully")


@settings_router.get("")
async def list_settings(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SettingService(db).list_all())


@settings_router.get("/{key}")
async def get_setting(key: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SettingService(db).get(key))


@settings_router.put("")
async def upsert_setting(payload: SettingUpsertRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SettingService(db).upsert(payload), "Setting saved successfully")


@public_router.get("/stats")
async def landing_stats(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await PublicStatsService(db).landing_page_stats())


@contact_router.post("")
async def submit_contact_message(payload: ContactMessageCreateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — no auth required. Powers the landing page contact form."""
    result = await ContactMessageService(db).create(payload)
    return success(result, "Thanks — we've received your message and will get back to you shortly.")


@contact_router.get("", dependencies=[Depends(require_admin)])
async def list_contact_messages(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    items, total = await ContactMessageService(db).list_all(pagination.page, pagination.page_size)
    return paginated(items, pagination.page, pagination.page_size, total)


@contact_router.patch("/{message_id}/status", dependencies=[Depends(require_admin)])
async def update_contact_message_status(message_id: str, payload: ContactMessageStatusUpdateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await ContactMessageService(db).update_status(message_id, payload.status), "Contact message status updated")
