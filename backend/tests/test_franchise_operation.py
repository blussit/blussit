"""Service-center `operation` (franchise / self-operated): validation matrix,
permissions and redaction, history + effective_from, fee received / pending,
kits, boot backfill. ARCHITECTURE.md §7.13 D18 / D19."""
import pytest
from bson import ObjectId

from tests import test_fix_core_helpers as h
from tests import test_franchise_helpers as fx
from tests.test_franchise_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio

SELF = {"type": "self_operated"}


async def _create(c, admin, operation, **kw):
    return await c.post(fx.CENTERS, json=fx.center_body(operation=operation, **kw), headers=admin)


async def test_create_defaults_to_self_operated(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        plain = await fx.make_center(c, admin)
        op = plain["operation"]
        assert op["type"] == "self_operated" and op["partner"] is None and op["revenue_share_percent"] is None
        assert op["captain_pay_managed_by"] == "blussit" and op["kits_given"] == 0 and op["effective_from"] == fx.today()
        assert op["default_captain_pay"]["basis"] is None
        assert plain["kits_assigned"] == 0 and plain["operation_history"] == []


async def test_validation_matrix(db, clean):
    admin = await fx.admin_h(db)
    ok_fr = fx.franchise_op()
    cases = [
        # franchise needs partner name + share > 0
        ({"type": "franchise"}, 400),
        ({"type": "franchise", "partner": fx.partner()}, 400),
        ({"type": "franchise", "revenue_share_percent": 70}, 400),
        ({"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 0}, 400),
        ({"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 100.5}, 422),
        ({"type": "franchise", "partner": fx.partner(), "revenue_share_percent": -1}, 422),
        ({"type": "franchise", "partner": fx.partner(name="x"), "revenue_share_percent": 70}, 422),
        ({"type": "franchise", "partner": fx.partner(phone="abc"), "revenue_share_percent": 70}, 422),
        ({"type": "franchise", "partner": fx.partner(email="nope"), "revenue_share_percent": 70}, 422),
        ({"type": "franchise", "partner": {"name": "Ravi", "address": "x"}, "revenue_share_percent": 70}, 422),
        # bounds
        (fx.franchise_op(kits_purchased=1001), 422),
        (fx.franchise_op(kits_purchased=-1), 422),
        (fx.franchise_op(franchise_fee_paise=-1), 422),
        (fx.franchise_op(kits_given=2), 400),
        (fx.franchise_op(captain_pay_managed_by="partner"), 422),
        (fx.franchise_op(unknown_field=1), 422),
        (fx.franchise_op(effective_from="2026-13-40"), 422),
        (fx.franchise_op(effective_from="tomorrow"), 422),
        (fx.franchise_op(effective_from=fx.day(-1)), 422),
        (fx.franchise_op(effective_from=fx.day(400)), 422),
        # self-operated forbids franchise-only fields
        ({"type": "self_operated", "partner": fx.partner()}, 400),
        ({"type": "self_operated", "revenue_share_percent": 70}, 400),
        ({"type": "self_operated", "franchise_fee_paise": 100}, 400),
        ({"type": "self_operated", "kits_purchased": 1}, 400),
        ({"type": "self_operated", "captain_pay_managed_by": "franchise"}, 400),
        ({"type": "self_operated", "kits_given": 1001}, 422),
        ({"type": "nonsense"}, 422),
        # default captain pay
        ({"default_captain_pay": {"basis": "salary"}}, 422),
        ({"default_captain_pay": {"basis": "salary", "salary_monthly_paise": 0}}, 422),
        ({"default_captain_pay": {"basis": "salary", "salary_monthly_paise": 10_00_000 * 100 + 1}}, 422),
        ({"default_captain_pay": {"basis": "salary", "salary_monthly_paise": 1500000, "commission_percent": 10}}, 422),
        ({"default_captain_pay": {"basis": "commission"}}, 422),
        ({"default_captain_pay": {"basis": "commission", "commission_percent": 70.5}}, 422),
        ({"default_captain_pay": {"basis": "commission", "commission_percent": -1}}, 422),
        ({"default_captain_pay": {"basis": "commission", "commission_percent": 20, "salary_monthly_paise": 5}}, 422),
        ({"default_captain_pay": {"salary_monthly_paise": 5}}, 422),
        ({"default_captain_pay": {"basis": "wage"}}, 422),
        # fee flags
        ({"type": "self_operated", "franchise_fee_received_now": True}, 400),
        (fx.franchise_op(franchise_fee_received_now=True), 400),  # no fee amount
    ]
    async with h.client() as c:
        for op, code in cases:
            r = await _create(c, admin, op)
            assert r.status_code == code, (op, r.status_code, r.text)
        # Edges that are allowed.
        for op in (
            ok_fr,
            fx.franchise_op(revenue_share_percent=100, kits_purchased=1000, franchise_fee_paise=1),
            fx.franchise_op(revenue_share_percent=0.5, captain_pay_managed_by="franchise"),
            {"type": "self_operated", "kits_given": 1000},
            {"default_captain_pay": {"basis": "salary", "salary_monthly_paise": 10_00_000 * 100}},
            {"default_captain_pay": {"basis": "commission", "commission_percent": 0}},
            {"default_captain_pay": {"basis": "commission", "commission_percent": 70}},
            fx.franchise_op(effective_from=fx.today()),
            fx.franchise_op(effective_from=fx.day(366)),
        ):
            r = await _create(c, admin, op)
            assert r.status_code == 200, (op, r.text)


async def test_update_validation_and_merge(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        fr = await fx.make_center(c, admin, fx.franchise_op(franchise_fee_paise=500000))
        cid = fr["id"]
        # A partial edit merges onto the stored operation and validates the whole.
        r = await fx.put_center(c, admin, cid, operation={"kits_purchased": 5})
        assert r.status_code == 200 and r.json()["data"]["operation"]["kits_purchased"] == 5
        assert r.json()["data"]["operation"]["revenue_share_percent"] == 70
        for bad in ({"revenue_share_percent": 0}, {"revenue_share_percent": 101}, {"partner": None}, {"kits_given": 3}, {"franchise_fee_received_now": True}):
            r = await fx.put_center(c, admin, cid, operation=bad)
            assert r.status_code in (400, 422), (bad, r.text)
        got = (await c.get(f"{fx.CENTERS}/{cid}", headers=admin)).json()["data"]["operation"]
        assert got["kits_purchased"] == 5 and got["revenue_share_percent"] == 70 and got["partner"]["name"] == "Ravi Sharma"
        # Self-operated -> franchise needs the partner and share.
        plain = await fx.make_center(c, admin)
        r = await fx.put_center(c, admin, plain["id"], operation={"type": "franchise"})
        assert r.status_code == 400
        r = await fx.put_center(c, admin, plain["id"], operation={"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 60})
        assert r.status_code == 200 and r.json()["data"]["operation"]["type"] == "franchise"
        # Franchise -> self-operated clears the franchise-only fields unless they are sent.
        r = await fx.put_center(c, admin, cid, operation={"type": "self_operated"})
        op = r.json()["data"]["operation"]
        assert r.status_code == 200 and op["type"] == "self_operated" and op["partner"] is None and op["revenue_share_percent"] is None
        assert op["franchise_fee_paise"] == 0 and op["kits_purchased"] == 0
        r = await fx.put_center(c, admin, cid, operation={"type": "self_operated", "revenue_share_percent": 50})
        assert r.status_code == 400


async def test_permissions_and_manager_redaction(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        fr = await fx.make_center(c, admin, fx.franchise_op(franchise_fee_paise=500000, captain_pay_managed_by="franchise"))
        cid = fr["id"]
        mgr = await h.manager(db, cid)
        other_cid, pin = await h.center(db)
        other_mgr = await h.manager(db, other_cid)
        cap = await h.captain(db, cid)
        cust = await h.customer(db, pin)
        # Only admin writes.
        for who, headers, code in (("manager", mgr["h"], 403), ("captain", cap["h"], 403), ("customer", cust["h"], 403), ("anon", {}, 401)):
            r = await c.post(fx.CENTERS, json=fx.center_body(operation=SELF), headers=headers)
            assert r.status_code == code, who
            r = await fx.put_center(c, headers, cid, operation={"kits_purchased": 9})
            assert r.status_code == code, who
            assert (await c.get(fx.CENTERS, headers=headers)).status_code == code, who
        assert (await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation"]["kits_purchased"] == 2
        # Admin sees everything.
        a = (await c.get(f"{fx.CENTERS}/{cid}", headers=admin)).json()["data"]["operation"]
        assert a["partner"]["phone"] == "9876543210" and a["franchise_fee_paise"] == 500000
        # The center's own manager: type, share, who pays captains, default pay, kits — no partner contact, fee, history.
        r = await c.get(f"{fx.CENTERS}/{cid}", headers=mgr["h"])
        assert r.status_code == 200
        data = r.json()["data"]
        op = data["operation"]
        assert op["type"] == "franchise" and op["revenue_share_percent"] == 70 and op["captain_pay_managed_by"] == "franchise"
        assert op["kits_purchased"] == 2 and "default_captain_pay" in op
        assert op["partner"] == {"name": "Ravi Sharma"}
        assert "franchise_fee_paise" not in op and "operation_history" not in data
        assert "9876543210" not in r.text and "ravi@example.com" not in r.text
        # Another center's manager is refused; captain and customer cannot read it.
        assert (await c.get(f"{fx.CENTERS}/{cid}", headers=other_mgr["h"])).status_code == 403
        assert (await c.get(f"{fx.CENTERS}/{cid}", headers=cap["h"])).status_code == 403
        assert (await c.get(f"{fx.CENTERS}/{cid}", headers=cust["h"])).status_code == 403
        # The public pincode lookup never carries the operation.
        r = await c.get(f"{fx.CENTERS}/lookup", params={"pincode": "452099"})
        assert "operation" not in r.text and "revenue_share" not in r.text


async def test_history_effective_from_and_audit(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        fr = await fx.make_center(c, admin, fx.franchise_op())
        cid = fr["id"]
        assert fr["operation_history"] == []
        # share % change is future-dated: history row + new value, audited.
        r = await fx.put_center(c, admin, cid, operation={"revenue_share_percent": 60, "effective_from": fx.day(5)})
        assert r.status_code == 200, r.text
        center = await db.service_centers.find_one({"_id": ObjectId(cid)})
        hist = center["operation_history"]
        assert len(hist) == 1 and hist[0]["changed"] == ["revenue_share_percent"]
        assert hist[0]["before"]["revenue_share_percent"] == 70 and hist[0]["after"]["revenue_share_percent"] == 60
        assert hist[0]["effective_from"] == fx.day(5) and hist[0]["by"]["role"] == "admin"
        from app.services.franchise_service import captain_pay_finance_impact, operation_as_of

        assert operation_as_of(center, fx.today())["revenue_share_percent"] == 70
        assert operation_as_of(center, fx.day(4))["revenue_share_percent"] == 70
        assert operation_as_of(center, fx.day(5))["revenue_share_percent"] == 60
        # Type, share and pay-manager each append; other edits do not.
        await fx.put_center(c, admin, cid, operation={"kits_purchased": 4, "partner": fx.partner(phone="9000000000")})
        assert len((await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation_history"]) == 1
        r = await fx.put_center(c, admin, cid, operation={"captain_pay_managed_by": "franchise"})
        assert r.status_code == 200
        center = await db.service_centers.find_one({"_id": ObjectId(cid)})
        assert len(center["operation_history"]) == 2 and center["operation_history"][1]["changed"] == ["captain_pay_managed_by"]
        assert captain_pay_finance_impact(center) is False
        assert captain_pay_finance_impact(center, fx.day(-1)) is True or captain_pay_finance_impact(center, "2000-01-01") is True
        r = await fx.put_center(c, admin, cid, operation={"type": "self_operated"})
        assert r.status_code == 200
        center = await db.service_centers.find_one({"_id": ObjectId(cid)})
        assert len(center["operation_history"]) == 3 and "type" in center["operation_history"][2]["changed"]
        assert captain_pay_finance_impact(center) is True
        # Audit rows (module franchise, with the center) carry before / after.
        rows = await db.audit_logs.find({"module": "franchise", "target_id": cid}).to_list(length=None)
        actions = [x["action"] for x in rows]
        assert actions.count("FRANCHISE_OPERATION_CHANGE") == 3 and "FRANCHISE_OPERATION_EDIT" in actions
        change = next(x for x in rows if x["details"].get("changed") == ["revenue_share_percent"])
        assert change["service_center_id"] == cid and change["details"]["before"]["revenue_share_percent"] == 70
        # An unchanged resend adds no history.
        n = len((await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation_history"])
        await fx.put_center(c, admin, cid, operation={"type": "self_operated"})
        assert len((await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation_history"]) == n


async def test_franchise_fee_received_now_vs_pending(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        now = await fx.make_center(c, admin, fx.franchise_op(franchise_fee_paise=20000000, franchise_fee_received_now=True, franchise_fee_method="upi"))
        entries = await db.finance_entries.find({"service_center_id": now["id"]}).to_list(length=None)
        assert len(entries) == 1
        e = entries[0]
        assert e["type"] == "credit" and e["category"] == "Franchise Fee" and e["amount_paise"] == 20000000 and e["source"] == "franchise_fee"
        assert e["source_id"].startswith(now["id"]) and e["locked"] is True
        led = await fx.ledger(db, now["id"])
        assert len(led) == 1 and led[0]["kind"] == "franchise_fee" and led[0]["method"] == "upi"
        # Pending: recorded on the center, no finance entry.
        pend = await fx.make_center(c, admin, fx.franchise_op(franchise_fee_paise=20000000))
        assert pend["operation"]["franchise_fee_paise"] == 20000000
        assert await db.finance_entries.count_documents({"service_center_id": pend["id"]}) == 0
        assert await db.franchise_ledger.count_documents({"service_center_id": pend["id"]}) == 0
        # The pending fee shows in the franchise list.
        items = {i["id"]: i for i in (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"]}
        assert items[pend["id"]]["fee_status"] == "pending" and items[pend["id"]]["fee_pending_paise"] == 20000000
        assert items[now["id"]]["fee_status"] == "received" and items[now["id"]]["fee_pending_paise"] == 0


async def test_kits_assigned_and_warning(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        fr = await fx.make_center(c, admin, fx.franchise_op(kits_purchased=1))
        cid = fr["id"]
        caps = []
        for provided, returned in ((True, None), (True, "2026-11-01"), (False, None)):
            cap = await h.captain(db, cid)
            await db.users.update_one({"_id": ObjectId(cap["id"])}, {"$set": {"pay": {"kit": {"provided": provided, "returned_on": returned}}}})
            caps.append(cap)
        got = (await c.get(f"{fx.CENTERS}/{cid}", headers=admin)).json()["data"]
        assert got["kits_assigned"] == 1 and got["captains_count"] == 3
        assert "3 captains but only 1 kit purchased" in got["kits_warning"]
        listed = (await c.get(fx.CENTERS, params={"search": fr["name"]}, headers=admin)).json()["data"]
        assert listed[0]["kits_assigned"] == 1
        # A warning only: nothing is blocked (more captains can still be added).
        await h.captain(db, cid)
        await fx.put_center(c, admin, cid, operation={"kits_purchased": 4})
        got = (await c.get(f"{fx.CENTERS}/{cid}", headers=admin)).json()["data"]
        assert got["kits_warning"] is None and got["captains_count"] == 4
        # Self-operated compares with kits given.
        so = await fx.make_center(c, admin, {"type": "self_operated", "kits_given": 1})
        for _ in range(2):
            await h.captain(db, so["id"])
        got = (await c.get(f"{fx.CENTERS}/{so['id']}", headers=admin)).json()["data"]
        assert "2 captains but only 1 kit given" in got["kits_warning"]


async def test_boot_backfill_is_idempotent_and_fills_only_missing(db, clean):
    from app.services.franchise_service import backfill_center_operation

    cid, _ = await h.center(db)
    try:
        legacy = await db.service_centers.find_one({"_id": ObjectId(cid)})
        assert "operation" not in legacy
        n = await backfill_center_operation(db)
        assert n >= 1
        op = (await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation"]
        assert op["type"] == "self_operated" and op["captain_pay_managed_by"] == "blussit" and op["partner"] is None
        # A center that already has an operation is never rewritten; a second run is a no-op.
        await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"operation.kits_given": 7}})
        await backfill_center_operation(db)
        assert (await db.service_centers.find_one({"_id": ObjectId(cid)}))["operation"]["kits_given"] == 7
        assert await backfill_center_operation(db) == 0
        # Read-time default for a center the backfill has not reached yet.
        cid2, _ = await h.center(db)
        admin = await fx.admin_h(db)
        async with h.client() as c:
            got = (await c.get(f"{fx.CENTERS}/{cid2}", headers=admin)).json()["data"]
        assert got["operation"]["type"] == "self_operated"
    finally:
        await db.service_centers.delete_many({"_id": {"$in": [ObjectId(cid)]}})
