from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin, require_manager_or_admin
from app.core.responses import success
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/manager-summary/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def manager_summary(service_center_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Section 20 — a manager's own-center daily KPI view."""
    ensure_own_center(current_user.role, current_user.service_center_id, service_center_id)
    return success(await AnalyticsService(db).manager_summary(service_center_id))


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def dashboard_summary(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await AnalyticsService(db).dashboard_summary())


@router.get("/booking-trends", dependencies=[Depends(require_admin)])
async def booking_trends(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await AnalyticsService(db).booking_trends(days))


@router.get("/service-centers", dependencies=[Depends(require_admin)])
async def service_center_summaries(db: AsyncIOMotorDatabase = Depends(get_db)):
    """The admin bookings drill-down's entry point (Section 10) — one row
    per center, not every booking loaded up front."""
    return success(await AnalyticsService(db).service_center_summaries())


@router.get("/vehicle-types", dependencies=[Depends(require_manager_or_admin)])
async def vehicle_type_breakdown(
    service_center_id: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Section 16 — per vehicle type KPI breakdown, the drill-down a click
    on a vehicle type (in its catalog page or a KPI card) opens. Admin sees
    platform-wide by default, optionally filtered to one center; a manager
    is always scoped to their own center regardless of what's passed."""
    center_id = current_user.service_center_id if current_user.role == "manager" else service_center_id
    return success(await AnalyticsService(db).vehicle_type_breakdown(center_id))


@router.get("/services", dependencies=[Depends(require_manager_or_admin)])
async def service_breakdown(
    service_center_id: str | None = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Section 17 — per service KPI breakdown, same scoping rule as above."""
    center_id = current_user.service_center_id if current_user.role == "manager" else service_center_id
    return success(await AnalyticsService(db).service_breakdown(center_id))
