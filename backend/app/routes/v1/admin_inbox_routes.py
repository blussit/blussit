"""Admin-only sidebar badges: how many NEW contact messages, custom-plan
requests and coverage requests are waiting, and the "seen" writes behind the
red dots. See services/admin_inbox_service.py."""
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.services import report_cache
from app.services.admin_inbox_service import CACHE_NAMESPACE, AdminInboxService

router = APIRouter(prefix="/admin", tags=["Admin Inbox"], dependencies=[Depends(require_admin)])


class MarkSeenRequest(BaseModel):
    # Omit (or null) to mark every unseen row of the section.
    ids: list[str] | None = Field(default=None, max_length=200)


@router.get("/nav-badges")
async def nav_badges(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Polled by every open admin tab (and refetched on a live ping): one
    shared count per instance every 15 s; any new entry or mark-seen expires
    it. Counts only — {contact_messages, plan_enquiries, coverage_requests}."""
    return success(await report_cache.cached((CACHE_NAMESPACE, "badges"), 15, AdminInboxService(db).counts))


@router.post("/inbox/{section}/seen")
async def mark_seen(
    section: str, payload: MarkSeenRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Marks the listed rows (or all, with no ids) of one section as seen.
    `section`: contact_messages | plan_enquiries | coverage_requests."""
    result = await AdminInboxService(db).mark_seen(section, payload.ids if payload else None, current_user.id)
    return success(result, "Marked as read")
