"""
Who changed which admin setting, when, from what to what — the audit trail
behind every "Last changed" line in the admin panel. Read-only; rows are
written by SettingRepository.upsert on every real change.
"""
from bson import ObjectId
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import get_db, require_admin
from app.core.responses import success
from app.repositories.content_repository import SettingRepository

router = APIRouter(prefix="/settings-history", tags=["Settings"], dependencies=[Depends(require_admin)])


@router.get("/{key}")
async def settings_history(key: str, limit: int = 30, db: AsyncIOMotorDatabase = Depends(get_db)):
    rows = await SettingRepository(db).history(key, limit=min(max(limit, 1), 100))
    actor_ids = [ObjectId(r["changed_by"]) for r in rows if r.get("changed_by") and ObjectId.is_valid(r["changed_by"])]
    names: dict[str, str | None] = {}
    if actor_ids:
        async for u in db.users.find({"_id": {"$in": actor_ids}}, {"full_name": 1}):
            names[str(u["_id"])] = u.get("full_name")
    return success(
        [
            {
                "changed_at": r["changed_at"],
                "changed_by": r.get("changed_by"),
                "changed_by_name": names.get(r.get("changed_by") or "") or ("System" if not r.get("changed_by") else "Admin"),
                "changes": r.get("changes") or {},
            }
            for r in rows
        ]
    )
