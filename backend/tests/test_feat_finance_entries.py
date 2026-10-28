"""Finance ledger — validation, admin-only access, create/edit/void with
audit rows, list filters + pagination, categories, CSV safety."""
import pytest

from tests import test_feat_finance_helpers as fh
from tests import test_fix_core_helpers as h
from tests.test_feat_finance_helpers import BASE, clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


async def test_validation_rules(db, clean):
    admin = await fh.admin_h(db)
    cases = [
        (fh.body(amount_paise=0), 422),
        (fh.body(amount_paise=-5), 422),
        (fh.body(amount_paise=1_00_00_000 * 100 + 1), 422),
        (fh.body(amount_paise=12.5), 422),
        (fh.body(type="refund"), 422),
        (fh.body(title=""), 422),
        (fh.body(title="   "), 422),
        (fh.body(title="x" * 121), 422),
        (fh.body(done_by=""), 422),
        (fh.body(done_by="y" * 101), 422),
        (fh.body(category="c" * 51), 422),
        (fh.body(description="d" * 501), 422),
        (fh.body(reference="r" * 81), 422),
        (fh.body(entry_date="28-10-2026"), 422),
        (fh.body(entry_date="2026-13-45"), 400),
        (fh.body(entry_date=fh.day(2)), 400),
        (fh.body(entry_date=fh.day(-(5 * 365 + 5))), 400),
    ]
    async with h.client() as c:
        for b, code in cases:
            r = await c.post(f"{BASE}/entries", json=b, headers=admin)
            assert r.status_code == code, (b, r.text)
        # Edges that are allowed.
        for b in (
            fh.body(amount_paise=1, title="t"),
            fh.body(amount_paise=1_00_00_000 * 100, title="x" * 120, done_by="y" * 100, category="c" * 50, description="d" * 500, reference="r" * 80),
            fh.body(entry_date=fh.day(1)),
            fh.body(entry_date=fh.day(-(5 * 365))),
            fh.body(category="  ", reference=""),
        ):
            r = await c.post(f"{BASE}/entries", json=b, headers=admin)
            assert r.status_code == 200, (b, r.text)
        # Defaults: date = today IST, blank category/reference -> null.
        e = await fh.make(c, admin)
        assert e["entry_date"] == fh.today() and e["amount_rupees"] == 15000.0
        e2 = await fh.make(c, admin, category="  ", reference=" ")
        assert e2["category"] is None and e2["reference"] is None


async def test_admin_only(db, clean):
    cid, _pin = await h.center(db)
    mgr = await h.manager(db, cid)
    cap = await h.captain(db, cid)
    cust = await h.customer(db, _pin)
    admin = await fh.admin_h(db)
    async with h.client() as c:
        e = await fh.make(c, admin)
        calls = [
            ("get", "/entries", None), ("get", "/entries.csv", None), ("get", "/summary", None), ("get", "/categories", None),
            ("post", "/entries", fh.body()), ("patch", f"/entries/{e['id']}", {"title": "x"}),
            ("post", f"/entries/{e['id']}/void", {"reason": "because"}),
        ]
        for who, headers, code in (("manager", mgr["h"], 403), ("captain", cap["h"], 403), ("customer", cust["h"], 403), ("anon", {}, 401)):
            for method, path, js in calls:
                r = await c.request(method.upper(), BASE + path, json=js, headers=headers)
                assert r.status_code == code, (who, method, path, r.status_code)
        # Nothing the refused callers sent changed the entry.
        got = (await c.get(f"{BASE}/entries", headers=admin)).json()
        assert got["meta"]["total"] == 1 and got["data"][0]["title"] == "Office rent"


async def test_create_edit_void_with_audit(db, clean):
    admin = await fh.admin_h(db)
    async with h.client() as c:
        e = await fh.make(c, admin, reference="UPI-1")
        r = await c.patch(f"{BASE}/entries/{e['id']}", json={"amount_paise": 2000000, "title": "Office rent Oct", "category": None}, headers=admin)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["amount_paise"] == 2000000 and d["title"] == "Office rent Oct" and d["category"] is None and d["reference"] == "UPI-1"
        # no-op edit writes no audit row
        await c.patch(f"{BASE}/entries/{e['id']}", json={"title": "Office rent Oct"}, headers=admin)
        # bad edits
        assert (await c.patch(f"{BASE}/entries/{e['id']}", json={"amount_paise": 0}, headers=admin)).status_code == 422
        assert (await c.patch(f"{BASE}/entries/{e['id']}", json={"title": None}, headers=admin)).status_code == 400
        assert (await c.patch(f"{BASE}/entries/{e['id']}", json={"entry_date": fh.day(5)}, headers=admin)).status_code == 400
        assert (await c.patch(f"{BASE}/entries/000000000000000000000000", json={"title": "x"}, headers=admin)).status_code == 404
        assert (await c.patch(f"{BASE}/entries/not-an-id", json={"title": "x"}, headers=admin)).status_code == 404

        assert (await c.post(f"{BASE}/entries/{e['id']}/void", json={"reason": "ab"}, headers=admin)).status_code == 422
        v = await c.post(f"{BASE}/entries/{e['id']}/void", json={"reason": "Entered twice"}, headers=admin)
        assert v.status_code == 200
        vd = v.json()["data"]
        assert vd["is_deleted"] and vd["delete_reason"] == "Entered twice" and vd["deleted_by"]
        # voided: cannot edit or void again; stays in the DB
        assert (await c.patch(f"{BASE}/entries/{e['id']}", json={"title": "z"}, headers=admin)).status_code == 409
        assert (await c.post(f"{BASE}/entries/{e['id']}/void", json={"reason": "again again"}, headers=admin)).status_code == 409
        assert await db.finance_entries.count_documents({}) == 1
        # hidden by default, visible with include_voided
        assert (await c.get(f"{BASE}/entries", headers=admin)).json()["meta"]["total"] == 0
        assert (await c.get(f"{BASE}/entries?include_voided=true", headers=admin)).json()["meta"]["total"] == 1

    rows = await db.audit_logs.find({"module": "finance"}).sort("_id", 1).to_list(length=10)
    assert [r["action"] for r in rows] == ["FINANCE_ENTRY_CREATE", "FINANCE_ENTRY_UPDATE", "FINANCE_ENTRY_VOID"]
    assert rows[0]["details"]["after"]["amount_paise"] == 1500000
    ch = rows[1]["details"]["changes"]
    assert ch["amount_paise"] == {"before": 1500000, "after": 2000000} and ch["category"]["after"] is None
    assert rows[2]["details"]["reason"] == "Entered twice" and rows[2]["target_id"] == e["id"]


async def test_list_filters_and_pagination(db, clean):
    admin = await fh.admin_h(db)
    async with h.client() as c:
        await fh.make(c, admin, title="Salary Ravi", category="Salary", done_by="Asha", entry_date=fh.day(-3))
        await fh.make(c, admin, title="Facebook ads", category="Marketing", done_by="Bhanu", entry_date=fh.day(-2))
        await fh.make(c, admin, title="Sold 2 wax kits", type="credit", category="Product Sale", done_by="Asha", entry_date=fh.day(-1), reference="INV-77")
        for i in range(5):
            await fh.make(c, admin, title=f"Misc {i}", category="Other", entry_date=fh.day(0))

        def total(qs):
            return c.get(f"{BASE}/entries?{qs}", headers=admin)

        assert (await total("type=credit")).json()["meta"]["total"] == 1
        assert (await total("type=debit")).json()["meta"]["total"] == 7
        assert (await total("category=salary")).json()["meta"]["total"] == 1
        assert (await total("done_by=asha")).json()["meta"]["total"] == 7
        assert (await total("done_by=Bhanu")).json()["meta"]["total"] == 1
        assert (await total("q=INV-77")).json()["meta"]["total"] == 1
        assert (await total("q=wax")).json()["meta"]["total"] == 1
        assert (await total(f"from={fh.day(-2)}&to={fh.day(-1)}")).json()["meta"]["total"] == 2  # inclusive both ends
        assert (await total(f"from={fh.day(0)}")).json()["meta"]["total"] == 5
        assert (await total(f"to={fh.day(-3)}")).json()["meta"]["total"] == 1
        # regex characters in a search are literal, not a pattern
        assert (await total("q=.*")).json()["meta"]["total"] == 0
        assert (await total("q=%28")).status_code == 200
        assert (await total(f"from={fh.day(0)}&to={fh.day(-5)}")).status_code == 400
        assert (await total("from=bad")).status_code == 422
        # newest first + pagination
        p1 = (await total("page=1&page_size=3")).json()
        assert p1["meta"]["total"] == 8 and len(p1["data"]) == 3 and p1["meta"]["total_pages"] == 3
        assert p1["data"][0]["entry_date"] == fh.day(0)
        dates = [e["entry_date"] for pg in (1, 2, 3) for e in (await total(f"page={pg}&page_size=3")).json()["data"]]
        assert dates == sorted(dates, reverse=True) and len(dates) == 8
        assert (await total("page=4&page_size=3")).json()["data"] == []
        for bad in ("page=0", "page_size=0", "page_size=101", "page=10001"):
            assert (await total(bad)).status_code == 422, bad

        cats = (await c.get(f"{BASE}/categories", headers=admin)).json()["data"]
        assert "Salary" in cats["suggested"] and "Marketing" in cats["used"] and "Other" in cats["all"]
        assert len(cats["all"]) == len({x.lower() for x in cats["all"]})


async def test_csv_is_formula_safe_and_audited(db, clean):
    from app.utils.csv_safe_finance import csv_safe_cell

    for bad in ("=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx"):
        assert csv_safe_cell(bad) == "'" + bad
    assert csv_safe_cell("Rent") == "Rent" and csv_safe_cell(None) == ""

    admin = await fh.admin_h(db)
    async with h.client() as c:
        await fh.make(c, admin, title='=HYPERLINK("http://x","y")', done_by="@evil", description="+1", reference="-9", category='He said "hi", ok')
        await fh.make(c, admin, title="Normal", done_by="Asha")
        r = await c.get(f"{BASE}/entries.csv", headers=admin)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        import csv, io

        rows = list(csv.reader(io.StringIO(r.text)))
        assert rows[0][:3] == ["Date", "Type", "Amount (INR)"] and len(rows) == 3
        evil = next(x for x in rows[1:] if x[3].startswith("'"))
        assert evil[3].startswith("'=") and evil[5] == "'@evil" and evil[6] == "'+1" and evil[7] == "'-9"
        assert evil[2] == "15000.00" and evil[4] == 'He said "hi", ok'
        # filters apply to the export
        r2 = await c.get(f"{BASE}/entries.csv?q=Normal", headers=admin)
        assert len(list(csv.reader(io.StringIO(r2.text)))) == 2
    acts = [x["action"] async for x in db.audit_logs.find({"module": "finance"}).sort("_id", 1)]
    assert acts.count("FINANCE_EXPORT") == 2
