from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import PaginationParams, get_db, require_admin
from app.core.responses import paginated
from app.repositories.audit_log_repository import AuditLogRepository
from app.utils.serializers import serialize_list

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"], dependencies=[Depends(require_admin)])


@router.get("")
async def list_audit_logs(module: Optional[str] = None, actor_id: Optional[str] = None, pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    filters: dict = {}
    if module:
        filters["module"] = module
    if actor_id:
        filters["actor_id"] = actor_id
    items, total = await AuditLogRepository(db).list_all(filters, pagination.page, pagination.page_size)
    return paginated(serialize_list(items), pagination.page, pagination.page_size, total)
