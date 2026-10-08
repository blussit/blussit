"""
OPS remediation (audit 2026-10-07) — admin actions vs live data.

ADM-01  PUT /settings wrote ANY key unchecked and unaudited (a bad
        booking_policy took booking down; pricing_config {per_km_rate:"abc"}
        500'd every booking): now whitelisted keys only, validated, audited.
ADM-03  DELETE /users/{id} hard-deleted accounts holding money or live
        obligations; DELETE /users/not-an-id answered "deleted".
ADM-06  deleting a service / vehicle type / category broke the live
        bookings, passes and plans that point at it.
ADM-08  update audits carried no before/after.
ADM-12  inventory, FAQ, testimonial and homepage edits were unaudited.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict, Field

from app.utils.timezone import now_ist
from tests.factories import (
    get_hatchback_type_id, make_captain, make_customer, make_manager, make_service_center,
)

pytestmark = pytest.mark.asyncio


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_seq = iter(range(1, 100_000))


async def _booking(db, cleanup, **fields) -> str:
    n = next(_seq)
    number = f"BKADM{n:05d}{str(ObjectId())[-6:]}"
    doc = {
        "booking_number": number, "status": "pending", "is_deleted": False, "customer_id": f"adm-cust-{number}",
        "visit_line_key": number, "scheduled_date": (now_ist() + timedelta(days=1)).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0),
        "scheduled_slot": "09:00-12:00", "created_at": datetime.now(timezone.utc),
    }
    doc.update(fields)
    res = await db.bookings.insert_one(doc)
    cleanup.append(("bookings", {"_id": res.inserted_id}))
    return number


@pytest.fixture
async def admin_h(db):
    admin = await db.users.find_one({"role": "admin"})
    return _auth(str(admin["_id"]), "admin")


# ---------------------------------------------------------------- ADM-01


@pytest.mark.parametrize("key,value,endpoint", [
    ("booking_policy", {"max_advance_days": 0}, "/booking-policy"),
    ("pricing_config", {"per_km_rate": "abc", "default_captain_service_fee": None}, "/pricing-config"),
    ("homepage_config", {"hero_headline": 5}, "/homepage-config"),
])
async def test_generic_settings_refuses_keys_with_a_dedicated_endpoint(db, admin_h, key, value, endpoint):
    before_doc = await db.settings.find_one({"key": key})
    before_audit = await db.audit_logs.count_documents({})
    async with _client() as c:
        res = await c.put("/api/v1/settings", headers=admin_h, json={"key": key, "value": value})
    assert res.status_code == 400, res.text
    assert endpoint in res.json()["message"]
    assert await db.settings.find_one({"key": key}) == before_doc  # untouched
    assert await db.audit_logs.count_documents({}) == before_audit


async def test_generic_settings_refuses_unknown_keys_and_non_admins(db, admin_h):
    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    try:
        async with _client() as c:
            unknown = await c.put("/api/v1/settings", headers=admin_h, json={"key": "anything_goes", "value": {"x": 1}})
            bad_key = await c.put("/api/v1/settings", headers=admin_h, json={"key": "Bad Key!", "value": {}})
            manager = await c.put("/api/v1/settings", headers=_auth(mgr, "manager", center), json={"key": "booking_policy", "value": {}})
        assert unknown.status_code == 400 and "anything_goes" in unknown.json()["message"]
        assert bad_key.status_code == 422
        assert manager.status_code == 403
        assert await db.settings.find_one({"key": "anything_goes"}) is None
    finally:
        await db.users.delete_one({"_id": ObjectId(mgr)})
        await db.service_centers.delete_one({"_id": ObjectId(center)})


class _OpsTestSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    support_hours: str = Field(min_length=3, max_length=40)
    max_items: int = Field(ge=1, le=10)


async def test_a_whitelisted_setting_is_validated_and_audited(db, admin_h, monkeypatch):
    from app.services import content_service

    monkeypatch.setitem(content_service.GENERIC_SETTINGS, "ops_test_setting", _OpsTestSetting)
    try:
        async with _client() as c:
            bad = await c.put("/api/v1/settings", headers=admin_h, json={"key": "ops_test_setting", "value": {"support_hours": "9-5", "max_items": "abc"}})
            extra = await c.put("/api/v1/settings", headers=admin_h, json={"key": "ops_test_setting", "value": {"support_hours": "9-5", "max_items": 2, "x": 1}})
            first = await c.put("/api/v1/settings", headers=admin_h, json={"key": "ops_test_setting", "value": {"support_hours": "9-5", "max_items": 2}})
            second = await c.put("/api/v1/settings", headers=admin_h, json={"key": "ops_test_setting", "value": {"support_hours": "9-5", "max_items": 5}})
        assert bad.status_code == 400 and "max_items" in bad.json()["message"]
        assert extra.status_code == 400
        assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
        assert (await db.settings.find_one({"key": "ops_test_setting"}))["value"] == {"support_hours": "9-5", "max_items": 5}
        rows = await db.audit_logs.find({"action": "UPDATE_SETTING", "target_id": "ops_test_setting"}).sort("created_at", 1).to_list(length=5)
        assert len(rows) == 2
        assert rows[-1]["details"]["changes"] == {"max_items": {"before": 2, "after": 5}}
    finally:
        await db.settings.delete_many({"key": "ops_test_setting"})
        await db.settings_history.delete_many({"key": "ops_test_setting"})
        await db.audit_logs.delete_many({"target_id": "ops_test_setting"})


# ---------------------------------------------------------------- ADM-03


async def _delete(c, admin_h, uid):
    return await c.delete(f"/api/v1/users/{uid}", headers=admin_h)


async def test_delete_refuses_captain_holding_wallet_money_or_open_withdrawal(db, cleanup, admin_h):
    center = await make_service_center(db)
    rich = await make_captain(db, center, wallet_balance=1500)
    owed = await make_captain(db, center, wallet_balance=0)
    for uid in (rich, owed):
        cleanup.append(("captain_wallets", {"captain_id": uid}))
        cleanup.append(("withdrawal_requests", {"captain_id": uid}))
        cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    await db.withdrawal_requests.insert_one({"captain_id": owed, "amount": 100, "status": "approved", "is_deleted": False})
    async with _client() as c:
        a = await _delete(c, admin_h, rich)
        b = await _delete(c, admin_h, owed)
    assert a.status_code == 400 and "wallet balance of ₹1500" in a.json()["message"], a.text
    assert "Inactive" in a.json()["message"]
    assert b.status_code == 400 and "withdrawal" in b.json()["message"], b.text
    assert await db.users.count_documents({"_id": {"$in": [ObjectId(rich), ObjectId(owed)]}}) == 2


async def test_delete_refuses_customer_with_active_pass_unpaid_booking_or_open_payment(db, cleanup, admin_h):
    with_pass, unpaid, paying = await make_customer(db), await make_customer(db), await make_customer(db)
    for uid in (with_pass, unpaid, paying):
        cleanup.append(("users", {"_id": ObjectId(uid)}))
        cleanup.append(("user_subscriptions", {"customer_id": uid}))
        cleanup.append(("payment_orders", {"customer_id": uid}))
    now = datetime.now(timezone.utc)
    await db.user_subscriptions.insert_one({"customer_id": with_pass, "status": "active", "end_date": now + timedelta(days=20), "is_deleted": False})
    number = await _booking(db, cleanup, customer_id=unpaid, status="awaiting_payment", payment_method="online")
    await db.payment_orders.insert_one({"customer_id": paying, "status": "created", "kind": "link", "created_at": now, "amount_paise": 34900})
    async with _client() as c:
        p = await _delete(c, admin_h, with_pass)
        u = await _delete(c, admin_h, unpaid)
        o = await _delete(c, admin_h, paying)
    assert p.status_code == 400 and "active plan" in p.json()["message"], p.text
    assert u.status_code == 400 and number in u.json()["message"], u.text
    assert o.status_code == 400 and "payment" in o.json()["message"], o.text
    assert await db.users.count_documents({"_id": {"$in": [ObjectId(x) for x in (with_pass, unpaid, paying)]}}) == 3


async def test_delete_refuses_a_centers_manager_and_a_society_captain(db, cleanup, admin_h):
    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    cap = await make_captain(db, center, wallet_balance=0)
    await db.service_centers.update_one({"_id": ObjectId(center)}, {"$set": {"manager_id": mgr}})
    soc = await db.societies.insert_one({"name": "Palm Court ADM", "service_center_id": center, "daily_captain_id": cap, "is_deleted": False})
    cleanup.append(("societies", {"_id": soc.inserted_id}))
    cleanup.append(("captain_wallets", {"captain_id": cap}))
    cleanup.append(("users", {"_id": {"$in": [ObjectId(mgr), ObjectId(cap)]}}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    async with _client() as c:
        m = await _delete(c, admin_h, mgr)
        s = await _delete(c, admin_h, cap)
    assert m.status_code == 400 and "manager slot" in m.json()["message"], m.text
    assert s.status_code == 400 and "Palm Court ADM" in s.json()["message"], s.text
    assert (await db.service_centers.find_one({"_id": ObjectId(center)}))["manager_id"] == mgr


async def test_delete_unknown_or_malformed_id_is_404_and_clean_account_is_deleted_and_audited(db, admin_h):
    clean = await make_customer(db)
    async with _client() as c:
        garbage = await _delete(c, admin_h, "not-an-id")
        missing = await _delete(c, admin_h, str(ObjectId()))
        ok = await _delete(c, admin_h, clean)
    assert garbage.status_code == 404, garbage.text
    assert missing.status_code == 404, missing.text
    assert ok.status_code == 200, ok.text
    assert await db.users.find_one({"_id": ObjectId(clean)}) is None
    row = await db.audit_logs.find_one({"action": "DELETE_USER", "target_id": clean})
    assert row and row["details"]["role"] == "customer"
    await db.audit_logs.delete_many({"target_id": clean})


# ---------------------------------------------------------------- ADM-06


async def _service(db, cleanup, **extra) -> str:
    cat = await db.categories.find_one({"is_deleted": {"$ne": True}})
    n = next(_seq)
    res = await db.services.insert_one({
        "name": f"OPS Service {n}", "slug": f"ops-service-{n}", "category_id": str(cat["_id"]), "price": 299.0,
        "is_active": True, "is_deleted": False, "created_at": datetime.now(timezone.utc), **extra,
    })
    cleanup.append(("services", {"_id": res.inserted_id}))
    return str(res.inserted_id)


async def test_service_delete_refused_while_bookings_passes_plans_or_combos_use_it(db, cleanup, admin_h):
    by_booking, by_pass, by_plan, by_combo, free = [await _service(db, cleanup) for _ in range(5)]
    number = await _booking(db, cleanup, service_ids=[by_booking], status="assigned")
    sub = await db.user_subscriptions.insert_one({"customer_id": "x", "service_id": by_pass, "status": "active", "is_deleted": False})
    plan = await db.subscription_plans.insert_one({"name": "OPS Plan Uses", "included_service_ids": [by_plan], "is_active": False, "is_deleted": False})
    combo = await db.combo_offers.insert_one({"name": "OPS Combo Uses", "service_ids": [by_combo], "is_active": True, "is_deleted": False})
    cleanup.append(("user_subscriptions", {"_id": sub.inserted_id}))
    cleanup.append(("subscription_plans", {"_id": plan.inserted_id}))
    cleanup.append(("combo_offers", {"_id": combo.inserted_id}))
    async with _client() as c:
        rb = await c.delete(f"/api/v1/services/{by_booking}", headers=admin_h)
        rp = await c.delete(f"/api/v1/services/{by_pass}", headers=admin_h)
        rl = await c.delete(f"/api/v1/services/{by_plan}", headers=admin_h)
        rc = await c.delete(f"/api/v1/services/{by_combo}", headers=admin_h)
        ok = await c.delete(f"/api/v1/services/{free}", headers=admin_h)
        gone = await c.delete(f"/api/v1/services/{free}", headers=admin_h)
    assert rb.status_code == 400 and number in rb.json()["message"], rb.text
    assert "Switch it off" in rb.json()["message"]
    assert rp.status_code == 400 and "pass" in rp.json()["message"], rp.text
    assert rl.status_code == 400 and "OPS Plan Uses" in rl.json()["message"], rl.text
    assert rc.status_code == 400 and "OPS Combo Uses" in rc.json()["message"], rc.text
    assert ok.status_code == 200, ok.text
    assert gone.status_code == 404
    still = await db.services.count_documents({"_id": {"$in": [ObjectId(x) for x in (by_booking, by_pass, by_plan, by_combo)]}, "is_deleted": False})
    assert still == 4


async def test_vehicle_type_delete_refused_while_in_use(db, cleanup, admin_h):
    n = next(_seq)
    used = (await db.vehicle_types.insert_one({"name": f"OPS Type {n}", "slug": f"ops-type-{n}", "is_active": True, "is_deleted": False})).inserted_id
    free = (await db.vehicle_types.insert_one({"name": f"OPS Free {n}", "slug": f"ops-free-{n}", "is_active": True, "is_deleted": False})).inserted_id
    cleanup.append(("vehicle_types", {"_id": {"$in": [used, free]}}))
    number = await _booking(db, cleanup, vehicle_type=str(used), status="pending")
    car = await db.vehicles.insert_one({"owner_id": "x", "vehicle_type": str(used), "registration_number": f"OPS{n}", "is_deleted": False})
    cleanup.append(("vehicles", {"_id": car.inserted_id}))
    async with _client() as c:
        refused = await c.delete(f"/api/v1/vehicle-types/{used}", headers=admin_h)
        ok = await c.delete(f"/api/v1/vehicle-types/{free}", headers=admin_h)
    assert refused.status_code == 400, refused.text
    assert number in refused.json()["message"] and "customer car" in refused.json()["message"]
    assert ok.status_code == 200, ok.text
    assert (await db.vehicle_types.find_one({"_id": used}))["is_deleted"] is False


async def test_category_delete_refused_while_services_use_it(db, cleanup, admin_h):
    n = next(_seq)
    cat = (await db.categories.insert_one({"name": f"OPS Cat {n}", "slug": f"ops-cat-{n}", "is_active": True, "is_deleted": False})).inserted_id
    empty = (await db.categories.insert_one({"name": f"OPS Empty {n}", "slug": f"ops-empty-{n}", "is_active": True, "is_deleted": False})).inserted_id
    cleanup.append(("categories", {"_id": {"$in": [cat, empty]}}))
    await _service(db, cleanup, category_id=str(cat))
    async with _client() as c:
        refused = await c.delete(f"/api/v1/categories/{cat}", headers=admin_h)
        ok = await c.delete(f"/api/v1/categories/{empty}", headers=admin_h)
    assert refused.status_code == 400 and "OPS Service" in refused.json()["message"], refused.text
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------- ADM-08


async def test_catalog_updates_audit_before_and_after(db, cleanup, admin_h):
    svc = await _service(db, cleanup)
    n = next(_seq)
    vt = (await db.vehicle_types.insert_one({"name": f"OPS VT {n}", "slug": f"ops-vt-{n}", "display_order": 1, "is_active": True, "is_deleted": False})).inserted_id
    cleanup.append(("vehicle_types", {"_id": vt}))
    cleanup.append(("audit_logs", {"target_id": {"$in": [svc, str(vt)]}}))
    async with _client() as c:
        r1 = await c.put(f"/api/v1/services/{svc}", headers=admin_h, json={"price": 1, "description": None})
        r2 = await c.put(f"/api/v1/vehicle-types/{vt}", headers=admin_h, json={"display_order": 7, "is_active": False})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    row = await db.audit_logs.find_one({"target_id": svc, "action": "UPDATE_SERVICE"})
    assert row["details"]["changes"] == {"price": {"before": 299.0, "after": 1.0}}
    row = await db.audit_logs.find_one({"target_id": str(vt), "action": "UPDATE_VEHICLE_TYPE"})
    assert row["details"]["changes"] == {"display_order": {"before": 1, "after": 7}, "is_active": {"before": True, "after": False}}


async def test_admin_user_update_audits_before_and_after(db, admin_h):
    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    try:
        async with _client() as c:
            res = await c.put(f"/api/v1/users/{mgr}", headers=admin_h, json={"full_name": "Renamed Mgr", "status": "active"})
        assert res.status_code == 200, res.text
        row = await db.audit_logs.find_one({"target_id": mgr, "action": "UPDATE_USER"})
        assert row["details"]["changes"]["full_name"]["after"] == "Renamed Mgr"
        assert "status" not in row["details"]["changes"]  # unchanged → not recorded
    finally:
        await db.audit_logs.delete_many({"target_id": mgr})
        await db.users.delete_one({"_id": ObjectId(mgr)})
        await db.service_centers.delete_one({"_id": ObjectId(center)})


# ---------------------------------------------------------------- ADM-12


async def test_inventory_and_content_edits_are_audited(db, cleanup, admin_h):
    center = await make_service_center(db)
    mgr = await make_manager(db, center)
    cleanup.append(("users", {"_id": ObjectId(mgr)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("inventory", {"service_center_id": center}))
    MA = _auth(mgr, "manager", center)
    async with _client() as c:
        item = (await c.post("/api/v1/inventory", headers=MA, json={
            "service_center_id": center, "item_name": "Shampoo", "unit": "litre", "quantity_available": 10, "reorder_level": 2,
        })).json()["data"]
        upd = await c.put(f"/api/v1/inventory/{item['id']}", headers=MA, json={"quantity_available": 3})
        dele = await c.delete(f"/api/v1/inventory/{item['id']}", headers=MA)
        faq = (await c.post("/api/v1/faqs", headers=admin_h, json={"question": "Q?", "answer": "A."})).json()["data"]
        fu = await c.put(f"/api/v1/faqs/{faq['id']}", headers=admin_h, json={"answer": "Better A."})
        fd = await c.delete(f"/api/v1/faqs/{faq['id']}", headers=admin_h)
        t = (await c.post("/api/v1/testimonials", headers=admin_h, json={"customer_name": "Ravi", "comment": "Great"})).json()["data"]
        tu = await c.put(f"/api/v1/testimonials/{t['id']}", headers=admin_h, json={"rating": 4})
        td = await c.delete(f"/api/v1/testimonials/{t['id']}", headers=admin_h)
        hp = await c.put("/api/v1/homepage-config", headers=admin_h, json={"banner_text": "OPS banner"})
    for res in (upd, dele, fu, fd, tu, td, hp):
        assert res.status_code == 200, res.text
    cleanup.append(("faqs", {"_id": ObjectId(faq["id"])}))
    cleanup.append(("testimonials", {"_id": ObjectId(t["id"])}))
    ids = [item["id"], faq["id"], t["id"], "homepage_config"]
    cleanup.append(("audit_logs", {"target_id": {"$in": ids}}))
    inv = await db.audit_logs.find_one({"target_id": item["id"], "action": "UPDATE_INVENTORY_ITEM"})
    assert inv["details"]["changes"] == {"quantity_available": {"before": 10.0, "after": 3.0}}
    assert inv.get("service_center_id") == center and inv["actor_id"] == mgr
    assert await db.audit_logs.find_one({"target_id": item["id"], "action": "DELETE_INVENTORY_ITEM"})
    fa = await db.audit_logs.find_one({"target_id": faq["id"], "action": "UPDATE_FAQ"})
    assert fa["details"]["changes"] == {"answer": {"before": "A.", "after": "Better A."}}
    assert await db.audit_logs.find_one({"target_id": faq["id"], "action": "DELETE_FAQ"})
    for action in ("CREATE_TESTIMONIAL", "UPDATE_TESTIMONIAL", "DELETE_TESTIMONIAL"):
        assert await db.audit_logs.find_one({"target_id": t["id"], "action": action}), action
    home = await db.audit_logs.find({"target_id": "homepage_config", "action": "UPDATE_HOMEPAGE_CONFIG"}).sort("created_at", -1).to_list(length=1)
    assert home and home[0]["details"]["changes"]["banner_text"]["after"] == "OPS banner"
    # restore the homepage banner text other tests may read
    await db.settings.update_one({"key": "homepage_config"}, {"$set": {"value.banner_text": ""}})
