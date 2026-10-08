"""ADM-13 — the public plan list never shows discontinued plans; only admin
and manager screens may ask for them (same rule as the other catalogue
lists)."""
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from tests.factories import make_customer, make_manager, make_subscription_plan


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(user_id: str, role: str, center: str | None = None) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center, 'tv': 0})}"}


@pytest.mark.asyncio
async def test_discontinued_plans_only_for_catalogue_editors(db, cleanup):
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    await db.subscription_plans.update_one({"_id": ObjectId(plan_id)}, {"$set": {"is_active": False}})
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))
    customer = await make_customer(db)
    manager = await make_manager(db, None)

    async with _client() as client:
        anon = await client.get("/api/v1/subscription-plans", params={"active_only": "false"})
        cust = await client.get("/api/v1/subscription-plans", params={"active_only": "false"}, headers=_auth(customer, "customer"))
        mgr = await client.get("/api/v1/subscription-plans", params={"active_only": "false"}, headers=_auth(manager, "manager"))

    ids = lambda res: {p["id"] for p in res.json()["data"]}  # noqa: E731
    assert anon.status_code == cust.status_code == mgr.status_code == 200
    assert plan_id not in ids(anon)
    assert plan_id not in ids(cust)
    assert plan_id in ids(mgr)
