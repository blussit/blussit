from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import PaginationParams, get_db, require_admin
from app.core.responses import paginated
from app.repositories.audit_log_repository import AuditLogRepository
from app.utils.serializers import serialize_list

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"], dependencies=[Depends(require_admin)])

_OBJECT_ID = r"^[0-9a-fA-F]{24}$"


@router.get("")
async def list_audit_logs(
    module: Optional[str] = Query(None, max_length=40),
    actor_id: Optional[str] = Query(None, pattern=_OBJECT_ID),
    actor_role: Optional[Literal["admin", "manager", "captain", "customer"]] = None,
    service_center_id: Optional[str] = Query(None, pattern=_OBJECT_ID),
    admin_in_center: Optional[bool] = None,
    pagination: PaginationParams = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Newest first. `admin_in_center=true` is "what admins did inside a
    center's queue" — entries AuditService attributed to a center while the
    actor was an admin. Rows carry the actor's and center's names so the
    page reads as a trail, not a list of ids."""
    filters: dict = {}
    if module:
        filters["module"] = module
    if actor_id:
        filters["actor_id"] = actor_id
    if actor_role:
        filters["actor_role"] = actor_role
    if service_center_id:
        filters["service_center_id"] = service_center_id
    if admin_in_center is not None:
        filters["admin_in_center"] = True if admin_in_center else {"$ne": True}
    items, total = await AuditLogRepository(db).list_all(filters, pagination.page, pagination.page_size)

    actor_ids = {i.get("actor_id") for i in items if isinstance(i.get("actor_id"), str) and ObjectId.is_valid(i["actor_id"])}
    center_ids = {i.get("service_center_id") for i in items if isinstance(i.get("service_center_id"), str) and ObjectId.is_valid(i["service_center_id"])}
    actors = {
        str(u["_id"]): u.get("full_name") or u.get("email") or u.get("phone")
        for u in await db.users.find({"_id": {"$in": [ObjectId(x) for x in actor_ids]}}, {"full_name": 1, "email": 1, "phone": 1}).to_list(length=len(actor_ids))
    } if actor_ids else {}
    centers = {
        str(c["_id"]): c.get("name")
        for c in await db.service_centers.find({"_id": {"$in": [ObjectId(x) for x in center_ids]}}, {"name": 1}).to_list(length=len(center_ids))
    } if center_ids else {}
    rows = serialize_list(items)
    for row in rows:
        row["actor_name"] = actors.get(row.get("actor_id") or "")
        row["service_center_name"] = centers.get(row.get("service_center_id") or "")
    return paginated(rows, pagination.page, pagination.page_size, total)
