"""
Society premium-wash scheduling HTTP surface — docs/SOCIETY_PLANS.md §9.

  manager  own center only (every route re-checks the society's / visit's /
           rule's / request's center — another center is 403)
  admin    everything (planner takes ?center_id=)
  captain  read-only: only the visit days they're on (anything else 404)
  resident their own scheduled washes + change requests on them
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    require_admin,
    require_captain,
    require_customer,
    require_manager_or_admin,
)
from app.core.exceptions import BadRequestException
from app.core.responses import success
from app.schemas.society_schedule_schema import (
    ChangeRequestCreate,
    ChangeRequestResolve,
    ExcludeRequest,
    ResidentRuleRequest,
    RotationRequest,
    ScheduleSettingsRequest,
    SocietyRuleRequest,
    VisitCreateRequest,
    VisitUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.society_schedule_service import SocietyScheduleService

router = APIRouter(prefix="/society-schedule", tags=["Society schedule"])

DATE = r"^\d{4}-\d{2}-\d{2}$"


async def _audit(db, user: CurrentUser, action: str, ref: str, details: dict | None = None) -> None:
    await AuditService(db).log_action(user.id, user.role, action, "society_schedule", ref, details)


def _center(user: CurrentUser, center_id: Optional[str]) -> str:
    """A manager always plans their own center; an admin picks one."""
    if user.role == "manager":
        if not user.service_center_id:
            raise BadRequestException("Your account isn't linked to a service center yet.")
        return user.service_center_id
    if not center_id:
        raise BadRequestException("Pick a service center.")
    return center_id


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings", dependencies=[Depends(require_manager_or_admin)])
async def get_settings(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyScheduleService(db).settings())


@router.put("/settings", dependencies=[Depends(require_admin)])
async def save_settings(payload: ScheduleSettingsRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyScheduleService(db).save_settings(payload, current_user.id)
    await _audit(db, current_user, "UPDATE_SOCIETY_SCHEDULE_SETTINGS", "schedule", payload.model_dump())
    return success(result, "Saved")


# ---------------------------------------------------------------------------
# Captain (read-only) — declared before /visits/{id} so the path wins
# ---------------------------------------------------------------------------


@router.get("/captain/visits", dependencies=[Depends(require_captain)])
async def captain_visits(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyScheduleService(db).captain_visits(current_user.id))


@router.get("/captain/visits/{visit_id}", dependencies=[Depends(require_captain)])
async def captain_visit(visit_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyScheduleService(db).captain_visit(current_user.id, visit_id))


# ---------------------------------------------------------------------------
# Resident
# ---------------------------------------------------------------------------


@router.get("/my", dependencies=[Depends(require_customer)])
async def my_schedule(society_id: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyScheduleService(db).my_schedule(current_user.id, society_id))


@router.post("/my/requests", dependencies=[Depends(require_customer)])
async def my_change_request(payload: ChangeRequestCreate, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyScheduleService(db).create_request(current_user.id, payload), "Request sent to your society manager")


# ---------------------------------------------------------------------------
# Planner (center) + one society
# ---------------------------------------------------------------------------


@router.get("/planner", dependencies=[Depends(require_manager_or_admin)])
async def planner(
    center_id: Optional[str] = None, start: Optional[str] = Query(None, pattern=DATE), end: Optional[str] = Query(None, pattern=DATE),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await SocietyScheduleService(db).planner(_center(current_user, center_id), start, end))


@router.post("/rotation", dependencies=[Depends(require_manager_or_admin)])
async def build_rotation(payload: RotationRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    center_id = _center(current_user, payload.center_id)
    result = await SocietyScheduleService(db).build_rotation(center_id, payload, current_user.id)
    if not payload.dry_run:
        await _audit(db, current_user, "SOCIETY_ROTATION", center_id, {"society_ids": payload.society_ids, "weekdays": payload.weekdays})
    return success(result, "Rotation saved" if not payload.dry_run else "Preview")


@router.get("/requests", dependencies=[Depends(require_manager_or_admin)])
async def list_requests(
    center_id: Optional[str] = None, status: Optional[str] = Query("pending", pattern=r"^(pending|approved|declined)$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    scope = current_user.service_center_id if current_user.role == "manager" else center_id
    if current_user.role == "manager" and not scope:
        raise BadRequestException("Your account isn't linked to a service center yet.")
    return success(await SocietyScheduleService(db).list_requests(scope, status))


@router.post("/requests/{request_id}/approve", dependencies=[Depends(require_manager_or_admin)])
async def approve_request(request_id: str, payload: ChangeRequestResolve, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    req = await service.request_for_staff(request_id, current_user)
    result = await service.approve_request(req, payload, current_user)
    await _audit(db, current_user, "APPROVE_SOCIETY_SCHEDULE_REQUEST", request_id)
    return success(result, "Approved — the resident is told")


@router.post("/requests/{request_id}/decline", dependencies=[Depends(require_manager_or_admin)])
async def decline_request(request_id: str, payload: ChangeRequestResolve, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    req = await service.request_for_staff(request_id, current_user)
    result = await service.decline_request(req, payload, current_user)
    await _audit(db, current_user, "DECLINE_SOCIETY_SCHEDULE_REQUEST", request_id)
    return success(result, "Declined — the resident is told")


@router.get("/societies/{society_id}", dependencies=[Depends(require_manager_or_admin)])
async def society_schedule(
    society_id: str, start: Optional[str] = Query(None, pattern=DATE), end: Optional[str] = Query(None, pattern=DATE),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = SocietyScheduleService(db)
    society = await service.society_for_staff(society_id, current_user)
    return success(await service.society_schedule(society, start, end))


@router.post("/societies/{society_id}/rules", dependencies=[Depends(require_manager_or_admin)])
async def create_society_rule(society_id: str, payload: SocietyRuleRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    society = await service.society_for_staff(society_id, current_user)
    rule = await service.create_rule(society, payload, "society", current_user.id)
    await _audit(db, current_user, "CREATE_SOCIETY_SCHEDULE_RULE", rule["id"], {"society_id": society_id})
    return success(rule, "Repeat visit saved")


@router.post("/societies/{society_id}/resident-rules", dependencies=[Depends(require_manager_or_admin)])
async def create_resident_rule(society_id: str, payload: ResidentRuleRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    society = await service.society_for_staff(society_id, current_user)
    rule = await service.create_rule(society, payload, "resident", current_user.id)
    await _audit(db, current_user, "CREATE_RESIDENT_SCHEDULE_RULE", rule["id"], {"society_id": society_id, "enrollment_id": payload.enrollment_id})
    return success(rule, "Repeat wash saved")


@router.post("/societies/{society_id}/visits", dependencies=[Depends(require_manager_or_admin)])
async def create_visit(society_id: str, payload: VisitCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    society = await service.society_for_staff(society_id, current_user)
    result = await service.create_visit(society, payload, current_user.id)
    await _audit(db, current_user, "CREATE_SOCIETY_VISIT", result["id"], {"society_id": society_id, "date": payload.date})
    return success(result, "Visit day added")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@router.put("/rules/{rule_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_rule(rule_id: str, payload: dict, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    rule = await service.rule_for_staff(rule_id, current_user)
    model = SocietyRuleRequest if rule["kind"] == "society" else ResidentRuleRequest
    from pydantic import ValidationError

    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:  # the body's shape depends on the rule's kind
        first = (exc.errors() or [{}])[0]
        raise BadRequestException(str(first.get("msg") or "Check the rule").removeprefix("Value error, ")) from exc
    result = await service.update_rule(rule, parsed, current_user.id)
    await _audit(db, current_user, "UPDATE_SOCIETY_SCHEDULE_RULE", rule_id)
    return success(result, "Saved — upcoming days re-planned")


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_manager_or_admin)])
async def delete_rule(rule_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    rule = await service.rule_for_staff(rule_id, current_user)
    await service.delete_rule(rule, current_user.id)
    await _audit(db, current_user, "DELETE_SOCIETY_SCHEDULE_RULE", rule_id)
    return success({"id": rule_id}, "Repeat removed — booked days stay")


# ---------------------------------------------------------------------------
# One visit day
# ---------------------------------------------------------------------------


@router.patch("/visits/{visit_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_visit(visit_id: str, payload: VisitUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    result = await service.update_visit(visit, payload, current_user)
    await _audit(db, current_user, "UPDATE_SOCIETY_VISIT", visit_id, payload.model_dump(exclude_unset=True))
    return success(result, "Saved")


@router.post("/visits/{visit_id}/skip", dependencies=[Depends(require_manager_or_admin)])
async def skip_visit(visit_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    result = await service.skip_visit(visit, current_user)
    await _audit(db, current_user, "SKIP_SOCIETY_VISIT", visit_id)
    return success(result, "Skipped")


@router.post("/visits/{visit_id}/restore", dependencies=[Depends(require_manager_or_admin)])
async def restore_visit(visit_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    result = await service.restore_visit(visit, current_user)
    await _audit(db, current_user, "RESTORE_SOCIETY_VISIT", visit_id)
    return success(result, "Restored")


@router.delete("/visits/{visit_id}", dependencies=[Depends(require_manager_or_admin)])
async def delete_visit(visit_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    result = await service.delete_visit(visit, current_user)
    await _audit(db, current_user, "DELETE_SOCIETY_VISIT", visit_id)
    return success(result, "Removed")


@router.post("/visits/{visit_id}/exclude", dependencies=[Depends(require_manager_or_admin)])
async def exclude_cars(visit_id: str, payload: ExcludeRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    result = await service.exclude(visit, payload.subscription_ids, payload.excluded, current_user)
    await _audit(db, current_user, "EXCLUDE_SOCIETY_VISIT_CARS", visit_id, payload.model_dump())
    return success(result, "Taken off this day" if payload.excluded else "Put back on this day")


@router.post("/visits/{visit_id}/book", dependencies=[Depends(require_manager_or_admin)])
async def book_visit_now(visit_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Book this day's washes now (or retry the ones that failed) instead of
    waiting for the sweep."""
    from datetime import date, timedelta

    from app.services.society_service import today_ist

    service = SocietyScheduleService(db)
    visit = await service.visit_for_staff(visit_id, current_user)
    policy = await service._policy()
    day = date.fromisoformat(visit["date"])
    if day <= today_ist():
        raise BadRequestException("Book a visit day at least a day ahead.")
    if day > today_ist() + timedelta(days=int(policy.get("max_advance_days", 7)) - 1):
        raise BadRequestException(f"Bookings open {int(policy.get('max_advance_days', 7))} days ahead — the sweep books this day {service_note(await service.settings())}.")
    result = await service.generate(visit_id, actor_id=current_user.id, retry=True)
    if result is None:
        raise BadRequestException("This day is skipped or being booked right now.")
    await _audit(db, current_user, "BOOK_SOCIETY_VISIT", visit_id, {"status": result.get("status")})
    booked = sum(len(a.get("sub_ids") or []) for a in result.get("allocations") or [] if a.get("status") == "booked")
    return success({"id": visit_id, "status": result.get("status"), "booked": booked, "error": result.get("generation_error")},
                   f"{booked} premium wash{'es' if booked != 1 else ''} booked")


def service_note(settings: dict) -> str:
    n = int(settings.get("generate_days_ahead") or 2)
    return f"{n} day{'s' if n != 1 else ''} before"
