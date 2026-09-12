from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.schemas.pricing_schema import PricingConfigRequest
from app.services.audit_service import AuditService
from app.services.pricing_service import PricingService

router = APIRouter(prefix="/pricing-config", tags=["Pricing"], dependencies=[Depends(require_admin)])


@router.get("")
async def get_pricing_config(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await PricingService(db).get_pricing_config())


@router.put("")
async def set_pricing_config(
    payload: PricingConfigRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    await PricingService(db).set_pricing_config(payload.per_km_rate, payload.default_captain_service_fee, updated_by=current_user.id)
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_PRICING_CONFIG", "settings", None, payload.model_dump())
    # Return the flat {per_km_rate, default_captain_service_fee} shape — same as GET —
    # rather than the raw settings document, which nests these under "value".
    result = await PricingService(db).get_pricing_config()
    return success(result, "Pricing configuration updated")
