"""ADM-12 — the admin's manual WhatsApp template sync is audit-logged."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.services.whatsapp_crm_service import WhatsAppCrmService

pytestmark = pytest.mark.asyncio


async def test_manual_template_sync_is_audited(db, monkeypatch):
    from app.main import app

    admin = await db.users.find_one({"role": "admin"})
    admin_id = str(admin["_id"])

    async def fake_sync(self):
        return {"synced": True, "count": 3}

    monkeypatch.setattr(WhatsAppCrmService, "sync_templates", fake_sync)
    before = await db.audit_logs.count_documents({"action": "WHATSAPP_SYNC_TEMPLATES"})
    headers = {"Authorization": f"Bearer {create_access_token(admin_id, 'admin', {'tv': admin.get('token_version', 0)})}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/whatsapp/crm/templates/sync", headers=headers)
    assert res.status_code == 200, res.text
    assert await db.audit_logs.count_documents({"action": "WHATSAPP_SYNC_TEMPLATES"}) == before + 1
