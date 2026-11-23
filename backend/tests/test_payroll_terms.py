"""Captain pay terms (§7.13 D1, D11, D14, D20): validation, who may edit what,
effective dating, staff creation with pay, the boot backfill, notifications."""
import pytest
from bson import ObjectId

from app.services.payroll_service import backfill_captain_pay
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import API, dues, make_payroll, phone, rig, second_center, set_pay

pytestmark = pytest.mark.asyncio


async def test_manager_sets_salary_terms_and_history_is_dated(db):
    s = await rig(db)
    async with h.client() as c:
        data = await make_payroll(c, s, "salary", salary_monthly_paise=3000000, weekly_off=6, paid_leaves_per_month=2, petrol_per_day_paise=5000)
        assert data["pay"]["basis"] == "salary" and data["pay"]["salary_monthly_paise"] == 3000000
        assert data["pay"]["paid_leaves_per_month"] == 2 and data["pay"]["petrol_per_day_paise"] == 5000
        assert data["legacy"] is False and len(data["pay_history"]) == 2  # the original terms + this change
        # a second edit keeps the first as history and only changes what was sent
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], salary_monthly_paise=3500000, reason="Raise")
        assert r.status_code == 200
        pay = r.json()["data"]["pay"]
        assert pay["salary_monthly_paise"] == 3500000 and pay["paid_leaves_per_month"] == 2
        assert len(r.json()["data"]["pay_history"]) == 2  # same-day edit replaces the same-day segment
    user = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
    assert user["pay"]["basis"] == "salary"


async def test_same_terms_again_is_a_no_op(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 30})
        before = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="commission", commission={"mode": "percent", "percent": 30})
        assert r.status_code == 200
    after = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
    assert after["pay"]["pay_history"] == before["pay"]["pay_history"]


@pytest.mark.parametrize("body", [
    {"basis": "salary", "salary_monthly_paise": -1},
    {"basis": "salary", "salary_monthly_paise": 100_000_001},
    {"basis": "commission", "commission": {"mode": "percent", "percent": 71}},
    {"basis": "commission", "commission": {"mode": "percent", "percent": -1}},
    {"basis": "commission", "commission": {"mode": "weird", "percent": 10}},
    {"basis": "salary", "weekly_off": 7},
    {"basis": "salary", "paid_leaves_per_month": 11},
    {"basis": "salary", "petrol_per_day_paise": -5},
    {"basis": "salary", "deposit_expected_paise": -5},
    {"basis": "nonsense"},
    {"basis": "salary", "kit": {"provided": True, "items": [{"name": "", "qty": 1}]}},
    {"basis": "salary", "kit": {"provided": True, "items": [{"name": "Vac", "qty": 51}]}},
    {"basis": "salary", "kit": {"provided": True, "items": [{"name": "Vac", "qty": 1}] * 31}},
    {"basis": "salary", "kit": {"provided": True, "items": [{"name": "Vac", "qty": 1, "value_paise": -1}]}},
    {"basis": "salary", "kit": {"provided": True, "items": [{"name": "Vac", "qty": 1, "serial": "x" * 41}]}},
])
async def test_terms_validation(db, body):
    s = await rig(db)
    async with h.client() as c:
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], **body)
        assert r.status_code in (400, 422), r.text


async def test_commission_cap_follows_the_business_setting(db):
    s = await rig(db)
    await db.business_settings.update_one({"_id": "singleton"}, {"$set": {"max_commission_percent": 50}}, upsert=True)
    try:
        async with h.client() as c:
            r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="commission", commission={"mode": "percent", "percent": 55})
            assert r.status_code == 400 and "50" in r.json()["message"]
            r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="commission", commission={"mode": "percent", "percent": 50})
            assert r.status_code == 200
    finally:
        await db.business_settings.update_one({"_id": "singleton"}, {"$unset": {"max_commission_percent": ""}})


async def test_permissions_matrix(db):
    s = await rig(db)
    other = await second_center(db)
    async with h.client() as c:
        body = {"basis": "salary", "salary_monthly_paise": 1000000}
        # own manager and admin may; the captain, a customer, and no token may not
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body, headers=s["mgr"]["h"])).status_code == 200
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body, headers=s["admin"]["h"])).status_code == 200
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body, headers=s["cap"]["h"])).status_code == 403
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body, headers=s["cu"]["h"])).status_code == 403
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body)).status_code == 401
        # another center's manager is refused, and so are reads
        assert (await c.put(f"{API}/captains/{s['cap']['id']}/terms", json=body, headers=other["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/captains/{s['cap']['id']}", headers=other["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/statement", headers=other["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/ledger", headers=other["mgr"]["h"])).status_code == 403
        assert (await c.get(f"{API}/captains/{s['cap']['id']}/commissions", headers=other["mgr"]["h"])).status_code == 403
        # a manager can't edit their own (or any manager's) pay — it isn't a captain
        r = await c.put(f"{API}/captains/{s['mgr']['id']}/terms", json=body, headers=s["mgr"]["h"])
        assert r.status_code == 400
        r = await c.put(f"{API}/captains/{s['mgr']['id']}/terms", json=body, headers=s["admin"]["h"])
        assert r.status_code == 400
        assert (await c.put(f"{API}/captains/{'0' * 24}/terms", json=body, headers=s["mgr"]["h"])).status_code == 404
        # lists are scoped: the other center's manager sees only their own captain
        rows = (await c.get(f"{API}/captains", headers=other["mgr"]["h"])).json()["data"]["rows"]
        assert {r["id"] for r in rows} == {other["cap"]["id"]}
        # ... the admin sees the center on request
        rows = (await c.get(f"{API}/captains", params={"center_id": s["center_id"]}, headers=s["admin"]["h"])).json()["data"]["rows"]
        assert s["cap"]["id"] in {r["id"] for r in rows}
        # a manager passing the other center's id still gets only their own
        rows = (await c.get(f"{API}/captains", params={"center_id": other["center_id"]}, headers=s["mgr"]["h"])).json()["data"]["rows"]
        assert {r["id"] for r in rows} == {s["cap"]["id"]}


async def test_effective_dating_and_bounds(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=3000000)
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], salary_monthly_paise=3600000, effective_from=h.day(10))
        assert r.status_code == 200
        user = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
        # still today's salary in force; the future segment is stored
        assert user["pay"]["salary_monthly_paise"] == 3000000
        assert [seg["effective_from"] for seg in user["pay"]["pay_history"]][-1] == h.day(10)
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], salary_monthly_paise=1, effective_from=h.day(-90))
        assert r.status_code == 400
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], salary_monthly_paise=1, effective_from=h.day(60))
        assert r.status_code == 400


async def test_legacy_captain_cant_be_moved_back_and_basis_default_is_legacy(db):
    s = await rig(db)
    async with h.client() as c:
        d = (await c.get(f"{API}/captains/{s['cap']['id']}", headers=s["mgr"]["h"])).json()["data"]
        assert d["basis"] == "legacy" and d["legacy"] is True
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 20})
        r = await set_pay(c, s["mgr"]["h"], s["cap"]["id"], basis="legacy")
        assert r.status_code == 400


async def test_captain_and_manager_are_told_about_a_terms_change(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "salary", as_admin=True, salary_monthly_paise=2500000)
    cap_notes = await db.notifications.find({"user_id": s["cap"]["id"], "title": "Your pay terms were updated"}).to_list(None)
    assert len(cap_notes) == 1 and "salary ₹25,000 a month" in cap_notes[0]["message"]
    mgr_notes = await db.notifications.find({"user_id": s["mgr"]["id"], "message": {"$regex": "An admin changed"}}).to_list(None)
    assert len(mgr_notes) == 1
    # a manager's own edit doesn't ring the manager
    async with h.client() as c:
        await set_pay(c, s["mgr"]["h"], s["cap"]["id"], salary_monthly_paise=2600000)
    assert await db.notifications.count_documents({"user_id": s["mgr"]["id"], "message": {"$regex": "An admin changed"}}) == 1
    audit = await db.audit_logs.find_one({"action": "PAYROLL_TERMS_UPDATE", "target_id": s["cap"]["id"]})
    assert audit and audit["service_center_id"] == s["center_id"]


async def test_create_staff_with_pay_defaults_and_requirements(db):
    s = await rig(db)
    await db.service_centers.update_one(
        {"_id": ObjectId(s["center_id"])},
        {"$set": {"operation": {"type": "self_operated", "default_captain_pay": {"basis": "salary", "salary_monthly_paise": 1800000}}}},
    )
    async with h.client() as c:
        n = ObjectId()
        base = {"full_name": "New Cap", "email": f"newcap.{n}@example.com", "phone": phone(),
                "password": "Passw0rd!x", "role": "captain"}
        # no pay → the center's default salary fills in
        r = await c.post("/api/v1/auth/staff", json=base, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        pay = r.json()["data"]["pay"]
        assert pay["basis"] == "salary" and pay["salary_monthly_paise"] == 1800000
        # explicit commission with a deposit + kit
        base2 = {**base, "email": f"newcap2.{n}@example.com", "phone": phone(),
                 "pay": {"basis": "commission", "commission": {"mode": "percent", "percent": 35}, "deposit_expected_paise": 500000,
                         "kit": {"provided": True, "items": [{"name": "Vacuum", "qty": 1, "serial": "V-1", "value_paise": 400000}]}}}
        r = await c.post("/api/v1/auth/staff", json=base2, headers=s["mgr"]["h"])
        assert r.status_code == 200, r.text
        pay = r.json()["data"]["pay"]
        assert pay["basis"] == "commission" and pay["commission"]["percent"] == 35 and pay["deposit_expected_paise"] == 500000
        assert pay["kit"]["items"][0]["name"] == "Vacuum"
        # over the cap is refused, nothing created
        base3 = {**base2, "email": f"newcap3.{n}@example.com", "phone": phone(),
                 "pay": {"basis": "commission", "commission": {"mode": "percent", "percent": 90}}}
        r = await c.post("/api/v1/auth/staff", json=base3, headers=s["mgr"]["h"])
        assert r.status_code == 400
        assert await db.users.count_documents({"email": base3["email"]}) == 0
    # without a default and without pay the HTTP route insists on a choice
    cid2, _ = await h.center(db)
    mgr2 = await h.manager(db, cid2)
    async with h.client() as c:
        r = await c.post("/api/v1/auth/staff", json={**base, "email": f"nodefault.{ObjectId()}@example.com", "phone": phone()}, headers=mgr2["h"])
        assert r.status_code == 400 and "salary or commission" in r.json()["message"]


async def test_boot_backfill_only_fills_missing_and_is_idempotent(db):
    s = await rig(db)
    # the factory captain has no pay block
    assert "pay" not in await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
    first = await backfill_captain_pay(db)
    assert first["filled"] >= 1
    user = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
    assert user["pay"]["basis"] == "legacy" and user["pay"]["start_date"]
    async with h.client() as c:
        await make_payroll(c, s, "salary", salary_monthly_paise=1000000)
    again = await backfill_captain_pay(db)
    assert again["filled"] == 0
    assert (await db.users.find_one({"_id": ObjectId(s["cap"]["id"])}))["pay"]["basis"] == "salary"
    assert await dues(db, s["cap"]["id"]) == 0
