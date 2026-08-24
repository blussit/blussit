from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.schemas.content_schema import HomepageConfigUpdateRequest
from app.services.audit_service import AuditService
from app.services.homepage_config_service import HomepageConfigService

router = APIRouter(prefix="/homepage-config", tags=["Homepage Config"])


@router.get("")
async def get_homepage_config(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public — the landing page reads this to decide what to show."""
    return success(await HomepageConfigService(db).get_config())


@router.put("", dependencies=[Depends(require_admin)])
async def set_homepage_config(
    payload: HomepageConfigUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await HomepageConfigService(db).set_config(payload.model_dump(exclude_unset=True))
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_HOMEPAGE_CONFIG", "settings", None, payload.model_dump(exclude_unset=True))
    return success(result, "Homepage settings updated")
