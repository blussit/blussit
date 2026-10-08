from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_catalogue_viewer,
    get_current_user,
    get_db,
    is_catalogue_editor,
    require_admin,
)
from app.core.responses import paginated, success
from app.schemas.content_schema import (
    ContactMessageCreateRequest,
    FaqCreateRequest,
    FaqUpdateRequest,
    SettingUpsertRequest,
    TestimonialCreateRequest,
    TestimonialUpdateRequest,
)
from app.services.audit_service import AuditService, field_changes
from app.services.content_service import ContactMessageService, FaqService, SettingService, TestimonialService

faq_router = APIRouter(prefix="/faqs", tags=["FAQs"])
testimonial_router = APIRouter(prefix="/testimonials", tags=["Testimonials"])
settings_router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=[Depends(require_admin)])
# Currently routeless (the unused, uncached /public/stats was removed); kept
# because main.py still includes it.
public_router = APIRouter(prefix="/public", tags=["Public Content"])
contact_router = APIRouter(prefix="/contact", tags=["Contact"])


# Hidden FAQs / non-featured testimonials (drafts, retired copy) are for
# the people who edit them — the flags are ignored for everyone else.
@faq_router.get("")
async def list_faqs(
    active_only: bool = True, viewer: CurrentUser | None = Depends(get_catalogue_viewer), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await FaqService(db).list_all(active_only or not is_catalogue_editor(viewer)))


@faq_router.post("", dependencies=[Depends(require_admin)])
async def create_faq(payload: FaqCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await FaqService(db).create(payload)
    await AuditService(db).log_action(current_user.id, current_user.role, "CREATE_FAQ", "faqs", result["id"])
    return success(result, "FAQ created successfully")


# Every content edit leaves an audit row with before/after (ADM-12).
@faq_router.put("/{faq_id}", dependencies=[Depends(require_admin)])
async def update_faq(faq_id: str, payload: FaqUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = FaqService(db)
    before = await service.repo.find_by_id(faq_id)
    result = await service.update(faq_id, payload)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_FAQ", "faqs", faq_id,
        {"changes": field_changes(before, result, payload.model_dump(exclude_unset=True))},
    )
    return success(result, "FAQ updated successfully")


@faq_router.delete("/{faq_id}", dependencies=[Depends(require_admin)])
async def delete_faq(faq_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = FaqService(db)
    before = await service.repo.find_by_id(faq_id)
    await service.delete(faq_id)
    await AuditService(db).log_action(current_user.id, current_user.role, "DELETE_FAQ", "faqs", faq_id, {"question": (before or {}).get("question")})
    return success(None, "FAQ deleted successfully")


@testimonial_router.get("")
async def list_testimonials(
    featured_only: bool = True, viewer: CurrentUser | None = Depends(get_catalogue_viewer), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await TestimonialService(db).list_all(featured_only or not is_catalogue_editor(viewer)))


@testimonial_router.post("", dependencies=[Depends(require_admin)])
async def create_testimonial(payload: TestimonialCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await TestimonialService(db).create(payload)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "CREATE_TESTIMONIAL", "testimonials", result["id"], {"customer_name": payload.customer_name},
    )
    return success(result, "Testimonial created successfully")


@testimonial_router.put("/{testimonial_id}", dependencies=[Depends(require_admin)])
async def update_testimonial(
    testimonial_id: str, payload: TestimonialUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = TestimonialService(db)
    before = await service.repo.find_by_id(testimonial_id)
    result = await service.update(testimonial_id, payload)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_TESTIMONIAL", "testimonials", testimonial_id,
        {"changes": field_changes(before, result, payload.model_dump(exclude_unset=True))},
    )
    return success(result, "Testimonial updated successfully")


@testimonial_router.delete("/{testimonial_id}", dependencies=[Depends(require_admin)])
async def delete_testimonial(testimonial_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = TestimonialService(db)
    before = await service.repo.find_by_id(testimonial_id)
    await service.delete(testimonial_id)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "DELETE_TESTIMONIAL", "testimonials", testimonial_id, {"customer_name": (before or {}).get("customer_name")},
    )
    return success(None, "Testimonial deleted successfully")


@settings_router.get("")
async def list_settings(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SettingService(db).list_all())


@settings_router.get("/{key}")
async def get_setting(key: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SettingService(db).get(key))


@settings_router.put("")
async def upsert_setting(payload: SettingUpsertRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Admin-only, whitelisted keys only (ADM-01): booking_policy,
    pricing_config and homepage_config are refused here — each has its own
    validated endpoint — and any unknown key is refused. Every write is
    audited with before/after and kept in settings_history."""
    result, changes = await SettingService(db).upsert(payload, updated_by=current_user.id)
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_SETTING", "settings", payload.key, {"key": payload.key, "changes": changes},
    )
    return success(result, "Setting saved successfully")


@contact_router.post("")
async def submit_contact_message(payload: ContactMessageCreateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — no auth required. Powers the landing page contact form."""
    result = await ContactMessageService(db).create(payload)
    return success(result, "Thanks — we've received your message and will get back to you shortly.")


@contact_router.get("", dependencies=[Depends(require_admin)])
async def list_contact_messages(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    items, total = await ContactMessageService(db).list_all(pagination.page, pagination.page_size)
    return paginated(items, pagination.page, pagination.page_size, total)
