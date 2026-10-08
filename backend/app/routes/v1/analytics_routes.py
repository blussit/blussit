from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.authz import ensure_own_center, manager_center_or_raise
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin, require_manager_or_admin
from app.core.responses import success
from app.services import report_cache
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class SiteVisitRequest(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


@router.post("/visit")
async def record_site_visit(payload: SiteVisitRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public beacon: counts this device once per IST day (rate-limited)."""
    from app.services.site_visit_service import SiteVisitService

    return success({"counted": await SiteVisitService(db).record(payload.device_id)})


@router.get("/visitors", dependencies=[Depends(require_admin)])
async def site_visitors(period: str | None = None, start: str | None = None, end: str | None = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Website visitors for the same periods as /kpis/* — unique
    device-days in range, today's devices, previous period, daily series."""
    from app.services.kpi_service import resolve_period
    from app.services.site_visit_service import SiteVisitService

    s, e, ps, pe = resolve_period(period, start, end)
    return success(await SiteVisitService(db).stats(s, e, ps, pe))


@router.get("/manager-summary/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def manager_summary(service_center_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Section 20 — a manager's own-center daily KPI view."""
    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    # Its quality strip aggregates the center's whole completed history —
    # a minute of staleness (the dashboard polls every minute anyway) keeps
    # that off the primary on every mount and live refresh.
    return success(await report_cache.cached(
        ("analytics", "manager-summary", service_center_id), _REPORT_TTL,
        lambda: AnalyticsService(db).manager_summary(service_center_id),
    ))


@router.get("/kpis/manager-overview/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def manager_kpi_overview(
    service_center_id: str,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """A manager's own combined bookings+plans revenue, bookings and plans
    sold — the Sales section on their KPI page, scoped to one center. See
    KpiService.manager_overview."""
    from app.services.kpi_service import KpiService, resolve_period

    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    s, e, ps, pe = resolve_period(period, start, end)
    return success(await KpiService(db).manager_overview(service_center_id, s, e, ps, pe))


@router.get("/manager-dashboard/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def manager_dashboard(
    service_center_id: str = Path(pattern=r"^[0-9a-fA-F]{24}$"),
    period: Literal["today", "yesterday", "7d", "30d", "this_month", "last_month"] | None = None,
    start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The manager's single home screen: period sales (same numbers as
    manager-overview), washes per service and per car type, plans sold,
    today's slot load, captain availability and the open alarms — ONE
    center, enforced here (a manager can only ever pass their own)."""
    from app.services.kpi_service import resolve_period
    from app.services.manager_dashboard_service import ManagerDashboardService

    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    s, e, ps, pe = resolve_period(period, start, end)
    return success(await ManagerDashboardService(db).dashboard(service_center_id, s, e, ps, pe))


# Admin report endpoints below are served from a short per-instance cache
# (report_cache): a minute of staleness on an all-time/period aggregate is
# fine; recomputing it for every open tab on every instance is not.
_REPORT_TTL = 60


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def dashboard_summary(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await report_cache.cached(("analytics", "dashboard"), _REPORT_TTL, AnalyticsService(db).dashboard_summary))


@router.get("/booking-trends", dependencies=[Depends(require_admin)])
async def booking_trends(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await AnalyticsService(db).booking_trends(days))


@router.get("/service-centers", dependencies=[Depends(require_admin)])
async def service_center_summaries(db: AsyncIOMotorDatabase = Depends(get_db)):
    """The admin bookings drill-down's entry point (Section 10) — one row
    per center, not every booking loaded up front."""
    return success(await report_cache.cached(("analytics", "centers"), 30, AnalyticsService(db).service_center_summaries))


@router.get("/vehicle-types", dependencies=[Depends(require_manager_or_admin)])
async def vehicle_type_breakdown(
    service_center_id: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Section 16 — per vehicle type KPI breakdown, the drill-down a click
    on a vehicle type (in its catalog page or a KPI card) opens. Admin sees
    platform-wide by default, optionally filtered to one center; a manager
    is always scoped to their own center regardless of what's passed."""
    center_id = manager_center_or_raise(current_user.role, current_user.service_center_id) or (
        service_center_id if current_user.role == "admin" else None)
    return success(await report_cache.cached(
        ("analytics", "vehicle_types", center_id), _REPORT_TTL, lambda: AnalyticsService(db).vehicle_type_breakdown(center_id)))


@router.get("/services", dependencies=[Depends(require_manager_or_admin)])
async def service_breakdown(
    service_center_id: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Section 17 — per service KPI breakdown, same scoping rule as above."""
    center_id = manager_center_or_raise(current_user.role, current_user.service_center_id) or (
        service_center_id if current_user.role == "admin" else None)
    return success(await report_cache.cached(
        ("analytics", "services", center_id), _REPORT_TTL, lambda: AnalyticsService(db).service_breakdown(center_id)))


# --------------------------------------------------------------------------
# Management KPI engine (admin dashboard analytics tabs). One generic route
# per section keeps the surface small; every section takes the same period
# arguments and computes its own previous-period comparison.
# --------------------------------------------------------------------------
from app.services.kpi_service import KpiService, resolve_period  # noqa: E402

_KPI_SECTIONS = ("overview", "business", "customers", "captains", "financial", "marketing", "operations", "areas")


_OBJECT_ID = r"^[0-9a-fA-F]{24}$"


@router.get("/kpis-explorer", dependencies=[Depends(require_admin)])
async def kpi_explorer(
    period: Literal["today", "yesterday", "7d", "30d", "this_month", "last_month"] | None = None,
    start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    service_center_id: str | None = Query(None, pattern=_OBJECT_ID),
    service_id: str | None = Query(None, pattern=_OBJECT_ID),
    vehicle_type: str | None = Query(None, pattern=_OBJECT_ID),
    source: str | None = Query(None, pattern=r"^[a-z_]{2,20}$"),
    granularity: Literal["auto", "day", "week", "month"] = "auto",
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The admin dashboard's interactive charts — one filtered slice,
    bucketed over time and broken down by service / car type / center /
    channel / plan. See KpiService.explorer for the definitions (the same
    ones every KPI section uses)."""
    s, e, ps, pe = resolve_period(period, start, end)
    key = ("kpi", "explorer", s, e, service_center_id, service_id, vehicle_type, source, granularity)
    return success(await report_cache.cached(key, _REPORT_TTL, lambda: KpiService(db).explorer(
        s, e, ps, pe, service_center_id=service_center_id, service_id=service_id,
        vehicle_type=vehicle_type, source=source, granularity=None if granularity == "auto" else granularity,
    )))


@router.get("/kpis/{section}", dependencies=[Depends(require_admin)])
async def kpi_section(
    section: str,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from app.core.exceptions import BadRequestException

    if section not in _KPI_SECTIONS:
        raise BadRequestException(f"Unknown KPI section '{section}'")
    s, e, ps, pe = resolve_period(period, start, end)
    return success(await report_cache.cached(
        ("kpi", section, s, e), _REPORT_TTL, lambda: getattr(KpiService(db), section)(s, e, ps, pe)))


@router.get("/business-settings", dependencies=[Depends(require_admin)])
async def get_business_settings(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await KpiService(db).get_settings())


@router.put("/business-settings")
async def update_business_settings(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from app.core.dependencies import require_admin as _  # role enforced below
    from app.core.exceptions import ForbiddenException
    from app.services.audit_service import AuditService

    if current_user.role != "admin":
        raise ForbiddenException("Admin only")
    updated = await KpiService(db).update_settings(payload)
    report_cache.invalidate("kpi")  # cost/target inputs feed the financial & overview sections
    await AuditService(db).log_action(
        current_user.id, current_user.role, "UPDATE_BUSINESS_SETTINGS", "analytics", "singleton",
        {"fields": sorted(payload.keys())},
    )
    return success(updated, message="Business settings updated")
