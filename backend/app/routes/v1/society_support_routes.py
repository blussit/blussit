"""
Society support HTTP surface (docs/SOCIETY_PLANS.md §9): resident issues
(support tickets tagged with the society) and the landing page's
"bring Blussit to our society" requests.

Who may do what:
- resident (customer): file an issue on a society they're enrolled in and
  read their own; nothing about other residents.
- manager: their center's societies' issues and their center's requests.
- admin: everything (requests no center serves included).
- public: POST a society request (rate-limited, deduped) and read the
  "from ₹X" price — never anyone's data.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_customer, require_manager_or_admin
from app.core.responses import success
from app.schemas.society_schema import SocietyIssueRequest, SocietyLeadRequest, SocietyLeadUpdateRequest
from app.services.audit_service import AuditService
from app.services.society_service import SocietyService
from app.services.society_support_service import SocietyIssueService, SocietyLeadService

issue_router = APIRouter(prefix="/society-issues", tags=["Society issues"])
lead_router = APIRouter(prefix="/society-leads", tags=["Society requests"])


# -- resident issues ------------------------------------------------------------


@issue_router.post("", dependencies=[Depends(require_customer)])
async def raise_society_issue(payload: SocietyIssueRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyIssueService(db).raise_issue(current_user.id, payload)
    return success(result, "Issue reported — your society manager will look into it")


@issue_router.get("/my", dependencies=[Depends(require_customer)])
async def my_society_issues(
    society_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await SocietyIssueService(db).my_issues(current_user.id, society_id))


@issue_router.get("/society/{society_id}", dependencies=[Depends(require_manager_or_admin)])
async def society_issues(
    society_id: str, status: Optional[str] = Query(None, pattern=r"^(open|in_progress|resolved|closed)$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    society = await SocietyService(db).society_for_actor(society_id, current_user.role, current_user.service_center_id)
    return success(await SocietyIssueService(db).society_issues(society, status))


# -- society requests (landing page) ----------------------------------------------


@lead_router.post("")
async def request_for_society(payload: SocietyLeadRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """PUBLIC. The same reply whether it's new or a repeat — it never says
    anything about who else asked."""
    await SocietyLeadService(db).capture(payload)
    return success(None, "Thanks! Our team will call you within a day.")


@lead_router.get("/info")
async def society_pitch_info(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success({"from_price": await SocietyLeadService(db).from_price()})


@lead_router.get("", dependencies=[Depends(require_manager_or_admin)])
async def list_society_requests(
    status: Optional[str] = Query(None, pattern=r"^(open|new|contacted|registered|closed)$"),
    center_id: Optional[str] = Query(None, max_length=40),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Manager: their center's requests. Admin: all (or one center, or
    `unassigned` — the pincodes no center serves)."""
    return success(await SocietyLeadService(db).list(current_user.role, current_user.service_center_id, status, center_id))


@lead_router.put("/{lead_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_society_request(
    lead_id: str, payload: SocietyLeadUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await SocietyLeadService(db).update(lead_id, payload, current_user.id, current_user.role, current_user.service_center_id)
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_SOCIETY_REQUEST", "society_leads", lead_id, payload.model_dump(exclude_unset=True))
    return success(result, "Saved")
