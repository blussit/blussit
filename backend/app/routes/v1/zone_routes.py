"""
Service-zone management (admin draws polygons) + the public pin-based
coverage check the booking wizard uses. Zone edits are audited.
"""
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin
from app.core.responses import success
from app.services.audit_service import AuditService
from app.services.zone_service import ZoneService

router = APIRouter(prefix="/service-zones", tags=["Service zones"])


class ZoneCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    service_center_id: str
    # [[lng, lat], ...] — GeoJSON order, straight from the map drawing.
    ring: list[list[float]] = Field(min_length=3, max_length=200)


class ZoneUpdateRequest(BaseModel):
    name: str | None = None
    ring: list[list[float]] | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class CoverageCheckRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    pincode: str | None = Field(default=None, max_length=10)


@router.get("", dependencies=[Depends(require_admin)])
async def list_zones(service_center_id: str | None = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await ZoneService(db).list_zones(service_center_id))


@router.post("", dependencies=[Depends(require_admin)])
async def create_zone(payload: ZoneCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    zone = await ZoneService(db).create_zone(payload.name, payload.service_center_id, payload.ring)
    await AuditService(db).log_action(current_user.id, current_user.role, "CREATE_SERVICE_ZONE", "zones", zone["id"], {"name": zone["name"]})
    return success(zone, message="Zone created")


@router.put("/{zone_id}", dependencies=[Depends(require_admin)])
async def update_zone(zone_id: str, payload: ZoneUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    zone = await ZoneService(db).update_zone(zone_id, name=payload.name, ring=payload.ring, is_active=payload.is_active)
    await AuditService(db).log_action(current_user.id, current_user.role, "UPDATE_SERVICE_ZONE", "zones", zone_id, {"fields": [k for k, v in payload.model_dump().items() if v is not None]})
    return success(zone, message="Zone updated")


@router.delete("/{zone_id}", dependencies=[Depends(require_admin)])
async def delete_zone(zone_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    await ZoneService(db).delete_zone(zone_id)
    await AuditService(db).log_action(current_user.id, current_user.role, "DELETE_SERVICE_ZONE", "zones", zone_id, {})
    return success({}, message="Zone removed")


@router.post("/coverage-check")
async def coverage_check(payload: CoverageCheckRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public: is this location served? The pin decides when zones exist;
    pincode is the fallback for pinless requests / a zone-less rollout.
    Returns the dispatch center so the wizard can load its slots."""
    from app.services.booking_service import BookingService
    from app.core.exceptions import BadRequestException

    fake_address = {"latitude": payload.latitude, "longitude": payload.longitude, "pincode": payload.pincode or ""}
    try:
        center, distance_km = await BookingService(db)._resolve_service_center(fake_address)
    except BadRequestException as exc:
        pin_required = "[PIN_REQUIRED]" in (exc.message or "")
        return success({"covered": False, "center": None, "pin_required": pin_required})
    loc = center.get("location") or {}
    return success({
        "covered": True,
        "center": {"id": str(center["_id"]), "name": center.get("name"), "city": loc.get("city"), "state": loc.get("state")},
        "distance_km": distance_km,
    })
