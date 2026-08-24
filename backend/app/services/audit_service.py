"""
Writes an immutable audit trail entry for sensitive actions performed
by staff (managers/admins). Every admin-facing mutation should call
`log_action` so the Audit Logs module in the PRD has real data.
"""
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.base import utcnow


class AuditService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["audit_logs"]

    async def log_action(
        self,
        actor_id: str,
        actor_role: str,
        action: str,
        module: str,
        target_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self.collection.insert_one(
            {
                "actor_id": actor_id,
                "actor_role": actor_role,
                "action": action,
                "module": module,
                "target_id": target_id,
                "details": details or {},
                "created_at": utcnow(),
            }
        )
