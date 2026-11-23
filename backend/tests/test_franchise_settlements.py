"""Franchise settlement statement and settlements (admin only): cash vs
online split, net payable, settlements -> locked Finance entries,
idempotency, void, fee / kit sale, and the Finance summary's
"Owed To Franchise Partners" lines (totals unchanged).
ARCHITECTURE.md §7.13 D18 / E."""
import pytest
from bson import ObjectId

from app.services import finance_service
from tests import test_fix_core_helpers as h
from tests import test_franchise_helpers as fx
from tests.test_franchise_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


async def _setup(db, c, admin, **op):
    """A franchise center (70 %) with a ₹1,000 cash job and a ₹500 online job."""
    cid = (await fx.make_center(c, admin, fx.franchise_op(**op)))["id"]
    cash = await fx.add_booking(db, cid, 1000)
    online = await fx.add_booking(db, cid, 500, payment_method="online", paid_cash=0.0, paid_online=500.0)
    for bid in (cash, online):
        await fx.sync(db, bid)
    return cid


def _body(kind, amount, **kw):
    return {"kind": kind, "amount_paise": amount, "method": "bank", "idempotency_key": kw.pop("key", f"key-{kind}-{amount}-12345"), **kw}


async def test_statement_numbers_cash_vs_online(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _setup(db, c, admin)
        r = await c.get(f"{fx.BASE}/centers/{cid}/statement", params={"month": fx.today()[:7]}, headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        m, a = d["month_view"], d["all_time"]
        assert m["bookings_completed"] == 2 and m["base_paise"] == 150000 and m["share_accrued_paise"] == 105000
        assert m["cash_collected_paise"] == 100000 and m["online_collected_paise"] == 50000 and m["cash_held_paise"] == 100000
        assert m["net_paise"] == 5000 and m["net"] == 50.0
        assert a["share_accrued_paise"] == 105000 and a["net_payable_paise"] == 5000 and a["payable_paise"] == 5000 and a["direction"] == "owed_to_partner"
        assert d["center"]["revenue_share_percent"] == 70 and d["center"]["partner"]["name"] == "Ravi Sharma"
        assert len(d["ledger"]) == 2 and d["settlements"] == []
        # The cash / online split is the Collections roll-up's own number.
        from app.services.payment_service import PaymentService

        roll = (await PaymentService(db).center_collections(cid, "admin", None, fx.today(), fx.today()))["totals"]
        assert roll["cash_amount"] * 100 == m["cash_collected_paise"] and roll["online_amount"] * 100 == m["online_collected_paise"]
        # A month with no jobs is empty; all-time is still there.
        r = await c.get(f"{fx.BASE}/centers/{cid}/statement", params={"month": "2020-01"}, headers=admin)
        m = r.json()["data"]["month_view"]
        assert m["bookings_completed"] == 0 and m["share_accrued_paise"] == 0 and m["cash_collected_paise"] == 0
        assert r.json()["data"]["all_time"]["share_accrued_paise"] == 105000
        # No month: all-time only.
        d = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]
        assert "month_view" not in d
        assert (await c.get(f"{fx.BASE}/centers/{cid}/statement", params={"month": "2026-13"}, headers=admin)).status_code == 422


async def test_partner_owes_blussit_when_cash_exceeds_share(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin, fx.franchise_op(revenue_share_percent=30)))["id"]
        bid = await fx.add_booking(db, cid, 1000)  # all cash
        await fx.sync(db, bid)
        a = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]["all_time"]
        assert a["share_accrued_paise"] == 30000 and a["cash_held_paise"] == 100000
        assert a["net_payable_paise"] == -70000 and a["receivable_paise"] == 70000 and a["payable_paise"] == 0 and a["direction"] == "owed_by_partner"
        # The partner remits ₹300: still owes ₹400.
        r = await c.post(f"{fx.BASE}/centers/{cid}/settlements", json=_body("receive_from_partner", 30000), headers=admin)
        assert r.status_code == 200, r.text
        a = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]["all_time"]
        assert a["net_payable_paise"] == -40000 and a["settled_received_paise"] == 30000
        e = await db.finance_entries.find_one({"service_center_id": cid, "source": "franchise_settlement"})
        assert e["type"] == "credit" and e["category"] == "Franchise Remittance" and e["amount_paise"] == 30000 and e["locked"] is True


async def test_settlements_post_finance_entries_idempotent_and_void(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _setup(db, c, admin)
        url = f"{fx.BASE}/centers/{cid}/settlements"
        body = _body("pay_partner", 5000, paid_on=fx.today(), note="Nov settlement")
        r = await c.post(url, json=body, headers=admin)
        assert r.status_code == 200, r.text
        row = r.json()["data"]
        assert row["settlement_kind"] == "pay_partner" and row["amount_paise"] == 5000 and row["replayed"] is False and row["finance_entry_id"]
        e = await db.finance_entries.find_one({"service_center_id": cid, "source": "franchise_settlement"})
        assert e["type"] == "debit" and e["category"] == "Franchise Payout" and e["amount_paise"] == 5000 and e["entry_date"] == fx.today()
        assert e["source_id"] == f"{cid}:{row['id']}" and "Ravi" not in e["title"] and str(e["_id"]) == row["finance_entry_id"]
        # Double submit: same row back, one ledger row, one finance entry.
        r2 = await c.post(url, json=body, headers=admin)
        assert r2.status_code == 200 and r2.json()["data"]["id"] == row["id"] and r2.json()["data"]["replayed"] is True
        assert await db.finance_entries.count_documents({"service_center_id": cid, "source": "franchise_settlement"}) == 1
        assert await db.franchise_ledger.count_documents({"service_center_id": cid, "kind": "settlement_pay"}) == 1
        # The same key for something else is a conflict.
        assert (await c.post(url, json={**body, "amount_paise": 6000}, headers=admin)).status_code == 409
        assert (await c.post(url, json={**body, "kind": "receive_from_partner"}, headers=admin)).status_code == 409
        # Nothing left to pay once settled.
        a = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]
        assert a["all_time"]["net_payable_paise"] == 0 and a["all_time"]["direction"] == "settled"
        assert [s["id"] for s in a["settlements"]] == [row["id"]]
        # Void: the row stays (voided), the finance entry is voided, the payable is back.
        vurl = f"{url}/{row['id']}/void"
        assert (await c.post(vurl, json={"reason": "x"}, headers=admin)).status_code == 422
        r = await c.post(vurl, json={"reason": "Wrong amount"}, headers=admin)
        assert r.status_code == 200 and r.json()["data"]["voided"] is True
        assert (await db.finance_entries.find_one({"_id": e["_id"]}))["is_deleted"] is True
        assert (await c.post(vurl, json={"reason": "Wrong amount"}, headers=admin)).status_code == 200  # idempotent
        a = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]["all_time"]
        assert a["net_payable_paise"] == 5000 and a["settled_paid_paise"] == 0
        # A share row can't be voided; an unknown id is 404; another center's id is 404.
        share_row = (await fx.ledger(db, cid, kind="share"))[0]
        assert (await c.post(f"{url}/{share_row['_id']}/void", json={"reason": "nope nope"}, headers=admin)).status_code == 404
        other = await fx.make_center(c, admin, fx.franchise_op())
        assert (await c.post(f"{fx.BASE}/centers/{other['id']}/settlements/{row['id']}/void", json={"reason": "nope nope"}, headers=admin)).status_code == 404
        # A re-recorded payment after the void is a new key.
        r = await c.post(url, json=_body("pay_partner", 5000, key="key-second-attempt"), headers=admin)
        assert r.status_code == 200 and r.json()["data"]["id"] != row["id"]
        # Audit rows.
        actions = [x["action"] for x in await db.audit_logs.find({"module": "franchise", "target_id": cid}).to_list(length=None)]
        assert actions.count("FRANCHISE_SETTLEMENT") == 2 and actions.count("FRANCHISE_SETTLEMENT_VOID") == 1


async def test_settlement_validation(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _setup(db, c, admin)
        plain = (await fx.make_center(c, admin))["id"]
        url = f"{fx.BASE}/centers/{cid}/settlements"
        for bad in (
            _body("pay_partner", 0), _body("pay_partner", -5), _body("pay_partner", 1_00_00_000 * 100 + 1), _body("pay_partner", 12.5),
            _body("loan", 100), {**_body("pay_partner", 100), "method": "crypto"}, {**_body("pay_partner", 100), "paid_on": "31-10-2026"},
            {**_body("pay_partner", 100), "paid_on": fx.day(5)}, {**_body("pay_partner", 100), "paid_on": fx.day(-5 * 366)},
            {**_body("pay_partner", 100), "idempotency_key": "short"}, {**_body("pay_partner", 100), "note": "n" * 301},
            {**_body("pay_partner", 100), "extra": 1}, {"kind": "pay_partner"},
        ):
            assert (await c.post(url, json=bad, headers=admin)).status_code == 422, bad
        # Not a franchise center / unknown center.
        assert (await c.post(f"{fx.BASE}/centers/{plain}/settlements", json=_body("pay_partner", 100), headers=admin)).status_code == 400
        assert (await c.get(f"{fx.BASE}/centers/{plain}/statement", headers=admin)).status_code == 400
        assert (await c.post(f"{fx.BASE}/centers/{ObjectId()}/settlements", json=_body("pay_partner", 100), headers=admin)).status_code == 404
        # Fee: none agreed -> refused.
        assert (await c.post(url, json=_body("franchise_fee", 100), headers=admin)).status_code == 400
        assert await db.finance_entries.count_documents({"service_center_id": {"$in": [cid, plain]}}) == 0


async def test_fee_and_kit_sale_settlements(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin, fx.franchise_op(franchise_fee_paise=20000000)))["id"]
        url = f"{fx.BASE}/centers/{cid}/settlements"
        r = await c.post(url, json=_body("franchise_fee", 12000000), headers=admin)
        assert r.status_code == 200, r.text
        e = await db.finance_entries.find_one({"service_center_id": cid, "source": "franchise_fee"})
        assert e["type"] == "credit" and e["category"] == "Franchise Fee" and e["amount_paise"] == 12000000
        row = next(i for i in (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"] if i["id"] == cid)
        assert row["fee_status"] == "partial" and row["fee_pending_paise"] == 8000000 and row["fee_received_paise"] == 12000000
        # More than pending is refused; the exact rest closes it.
        assert (await c.post(url, json=_body("franchise_fee", 8000001, key="key-over-pending"), headers=admin)).status_code == 400
        assert (await c.post(url, json=_body("franchise_fee", 8000000, key="key-rest-of-fee"), headers=admin)).status_code == 200
        row = next(i for i in (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"] if i["id"] == cid)
        assert row["fee_status"] == "received" and row["fee_pending_paise"] == 0
        # A voided fee payment is pending again.
        fee_row = (await fx.ledger(db, cid, kind="franchise_fee"))[0]
        assert (await c.post(f"{url}/{fee_row['_id']}/void", json={"reason": "Cheque bounced"}, headers=admin)).status_code == 200
        row = next(i for i in (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"] if i["id"] == cid)
        assert row["fee_pending_paise"] == 12000000 and row["fee_status"] == "partial"
        # Kit sale: a credit, no effect on the payable.
        r = await c.post(url, json=_body("kit_sale", 500000), headers=admin)
        assert r.status_code == 200
        e = await db.finance_entries.find_one({"service_center_id": cid, "source": "franchise_kit_sale"})
        assert e["type"] == "credit" and e["category"] == "Kit Sale" and e["amount_paise"] == 500000
        row = next(i for i in (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"] if i["id"] == cid)
        assert row["net_payable_paise"] == 0 and row["kit_sales_paise"] == 500000


async def test_admin_only(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _setup(db, c, admin)
        mgr = await h.manager(db, cid)
        cap = await h.captain(db, cid)
        _other, pin = await h.center(db)
        cust = await h.customer(db, pin)
        sid = str(ObjectId())
        calls = [
            ("get", "/centers", None), ("get", f"/centers/{cid}/statement", None),
            ("post", f"/centers/{cid}/settlements", _body("pay_partner", 100)),
            ("post", f"/centers/{cid}/settlements/{sid}/void", {"reason": "because"}),
        ]
        for who, headers, code in (("manager", mgr["h"], 403), ("captain", cap["h"], 403), ("customer", cust["h"], 403), ("anon", {}, 401)):
            for method, path, js in calls:
                r = await c.request(method.upper(), fx.BASE + path, json=js, headers=headers)
                assert r.status_code == code, (who, method, path, r.status_code)
        assert await db.franchise_ledger.count_documents({"service_center_id": cid, "kind": "settlement_pay"}) == 0
        # The franchise list carries kits vs captains.
        items = (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"]
        row = next(i for i in items if i["id"] == cid)
        assert row["kits_purchased"] == 2 and row["captains_count"] == 1 and row["kits_assigned"] == 0 and row["kits_warning"] is None
        assert row["bookings_completed"] == 2 and row["share_accrued_paise"] == 105000 and row["payable_paise"] == 5000


async def test_finance_summary_owed_lines_leave_totals_alone(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin, fx.franchise_op()))["id"]
        cash = await fx.add_booking(db, cid, 1000)
        online = await fx.add_booking(db, cid, 500, payment_method="online", paid_cash=0.0, paid_online=500.0)

        async def summary():
            finance_service.clear_cache()
            return (await c.get("/api/v1/admin/finance/summary", headers=admin)).json()["data"]

        base = await summary()
        for key in ("franchise_payable_paise", "franchise_receivable_paise", "franchise_payable", "franchise_receivable"):
            assert key in base
        # Accrual alone: the owed line moves; totals and balance do not.
        for bid in (cash, online):
            await fx.sync(db, bid)
        after = await summary()
        assert after["franchise_payable_paise"] - base["franchise_payable_paise"] == 5000
        assert after["franchise_payable"] == after["franchise_payable_paise"] / 100
        for key in ("total_balance_paise", "credits_paise", "debits_paise", "net_period_paise", "bookings_earning_paise"):
            assert after[key] == base[key], key
        # Paying the partner: debit appears, the owed line drops.
        await c.post(f"{fx.BASE}/centers/{cid}/settlements", json=_body("pay_partner", 5000), headers=admin)
        paid = await summary()
        assert paid["franchise_payable_paise"] == base["franchise_payable_paise"]
        assert paid["debits_paise"] - after["debits_paise"] == 5000 and paid["total_balance_paise"] == after["total_balance_paise"] - 5000
        # A partner that owes Blussit shows as receivable, not as negative payable.
        c2 = (await fx.make_center(c, admin, fx.franchise_op(revenue_share_percent=10)))["id"]
        b2 = await fx.add_booking(db, c2, 1000)
        await fx.sync(db, b2)
        owed = await summary()
        assert owed["franchise_receivable_paise"] - paid["franchise_receivable_paise"] == 90000
        assert owed["franchise_payable_paise"] == paid["franchise_payable_paise"]
        # Finance entries can be filtered by center and source and are locked.
        r = await c.get("/api/v1/admin/finance/entries", params={"service_center_id": cid, "source": "franchise_settlement"}, headers=admin)
        assert r.json()["meta"]["total"] == 1
        e = r.json()["data"][0]
        r = await c.patch(f"/api/v1/admin/finance/entries/{e['id']}", json={"title": "edit"}, headers=admin)
        assert r.status_code in (400, 409, 403, 422)
