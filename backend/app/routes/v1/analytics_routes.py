from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import get_db, require_admin
from app.core.responses import success
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"], dependencies=[Depends(require_admin)])


@router.get("/dashboard")
async def dashboard_summary(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await AnalyticsService(db).dashboard_summary())


@router.get("/booking-trends")
async def booking_trends(days: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await AnalyticsService(db).booking_trends(days))
