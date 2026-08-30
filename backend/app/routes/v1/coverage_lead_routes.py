from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.dependencies import PaginationParams, get_db, require_admin
from app.core.responses import paginated, success
from app.services.coverage_lead_service import CoverageLeadService

router = APIRouter(prefix="/coverage-leads", tags=["Coverage Leads"])


class CoverageLeadCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=15)
    pincode: str = Field(min_length=4, max_length=10)
    city_area: str | None = Field(default=None, max_length=150)
    service_interest: str | None = Field(default=None, max_length=150)
    latitude: float | None = None
    longitude: float | None = None


@router.post("")
async def capture_lead(payload: CoverageLeadCreateRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """PUBLIC — a landing-page visitor from an uncovered area leaving their
    details. No auth by design: this fires exactly at the moment we can't
    serve them, before any account could exist."""
    await CoverageLeadService(db).capture(
        payload.name, payload.phone, payload.pincode, payload.city_area, payload.service_interest, payload.latitude, payload.longitude
    )
    return success(None, "Thanks! We'll let you know as soon as we launch in your area.")


@router.get("", dependencies=[Depends(require_admin)])
async def list_leads(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    items, total = await CoverageLeadService(db).list_for_admin(pagination.page, pagination.page_size)
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/summary", dependencies=[Depends(require_admin)])
async def leads_summary(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await CoverageLeadService(db).summary_for_admin())
