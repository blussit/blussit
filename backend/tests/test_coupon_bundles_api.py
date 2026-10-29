"""Coupon bundles — the admin API: access, CSV preview, import, the bundle
table (status filter / sort / search / pages), export, edit as one unit,
single-code switch, delete, audit trail, and a 100,000-code import."""
import csv
import io
import time
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.services.coupon_service import CouponService
from tests.factories import make_captain, make_customer, make_manager, make_service_center

pytestmark = pytest.mark.asyncio
API = "/api/v1/coupons/bundles"


def _auth(user_id: str, role: str, center_id: str | None = None, tv: int = 0) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': tv})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120)


@pytest.fixture
async def admin(db):
    a = await db.users.find_one({"role": "admin"})
    return _auth(str(a["_id"]), "admin", a.get("service_center_id"), a.get("token_version", 0))


def csv_file(text: str, name="codes.csv", kind="text/csv"):
    return {"file": (name, text.encode() if isinstance(text, str) else text, kind)}


def future(days=30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def body(codes, **kw):
    base = {"name": f"Bundle {ObjectId()}", "coupon_type": "flat", "value": 100, "valid_until": future(), "uses_per_coupon": 1, "codes": codes}
    base.update(kw)
    return base


async def create(client, admin, codes, cleanup=None, **kw):
    res = await client.post(API, json=body(codes, **kw), headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    if cleanup is not None:
        bid = data["bundle"]["id"]
        cleanup.append(("coupon_codes", {"bundle_id": bid}))
        cleanup.append(("coupon_bundles", {"_id": ObjectId(bid)}))
        cleanup.append(("coupon_usages", {"bundle_id": bid}))
    return data


# ------------------------------------------------------------------ access


async def test_every_route_is_admin_only(db, cleanup, admin):
    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    customer = await make_customer(db)
    manager = await make_manager(db, center)
    captain = await make_captain(db, center)
    cleanup += [("users", {"_id": ObjectId(i)}) for i in (customer, manager, captain)]
    who = {
        "anonymous": ({}, 401),
        "customer": (_auth(customer, "customer"), 403),
        "manager": (_auth(manager, "manager", center), 403),
        "captain": (_auth(captain, "captain", center), 403),
    }
    bid = str(ObjectId())
    calls = [
        ("get", API, {}), ("get", f"{API}/sample.csv", {}), ("get", f"{API}/{bid}", {}), ("post", API, {"json": body(["ABCD1"])}),
        ("post", f"{API}/preview", {"files": csv_file("Code\nABCD1\n")}), ("put", f"{API}/{bid}", {"json": {"name": "x2"}}),
        ("delete", f"{API}/{bid}", {}), ("get", f"{API}/{bid}/codes", {}), ("get", f"{API}/{bid}/export.csv", {}),
        ("post", f"{API}/{bid}/codes", {"json": {"codes": ["ABCD1"]}}), ("put", f"{API}/{bid}/codes/{bid}", {"json": {"is_active": False}}),
        ("get", f"{API}/{bid}/codes/{bid}/uses", {}),
    ]
    async with _client() as client:
        for label, (headers, expected) in who.items():
            for method, url, kw in calls:
                res = await getattr(client, method)(url, headers=headers, **kw)
                assert res.status_code == expected, (label, method, url, res.status_code)
        # An admin gets through to a real answer (404 for the made-up id).
        assert (await client.get(f"{API}/{bid}", headers=admin)).status_code == 404


# ------------------------------------------------------------------ CSV preview


async def test_sample_csv_and_format_text(admin):
    async with _client() as client:
        res = await client.get(f"{API}/sample.csv", headers=admin)
    assert res.status_code == 200 and res.text.startswith("Code\r\n") and "text/csv" in res.headers["content-type"]


async def test_preview_flags_every_problem_and_writes_nothing(db, cleanup, admin):
    async with _client() as client:
        first = await create(client, admin, ["TAKEN-0001"], cleanup)
        db_before = await db.coupon_codes.count_documents({})
        text = "Code\r\ntaken-0001\r\nfresh-0001\r\nfresh-0001\r\nab\r\nhas space\r\n\r\nOK_2-0003\r\n"
        res = await client.post(f"{API}/preview", files=csv_file(text), headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    by_code = {r[1]: r[2] for r in data["rows"]}
    assert by_code["TAKEN-0001"] == "Already Exists"
    assert by_code["AB"] == "Too Short (Min 4)"
    assert by_code["HAS SPACE"] == "Invalid Characters"
    assert by_code["OK_2-0003"] is None
    assert [r[2] for r in data["rows"] if r[1] == "FRESH-0001"] == [None, "Duplicate In File"]
    assert data["total"] == 6 and data["ok"] == 2 and data["problems"] == 4
    assert "One column only" in data["format"]
    assert await db.coupon_codes.count_documents({}) == db_before
    assert first["added"] == 1


async def test_preview_also_flags_codes_that_exist_as_regular_coupons(db, cleanup, admin):
    now = datetime.now(timezone.utc)
    res = await db.coupons.insert_one({"code": "REGULAR-77", "coupon_type": "flat", "value": 10, "valid_from": now, "valid_until": now, "is_active": True})
    cleanup.append(("coupons", {"_id": res.inserted_id}))
    async with _client() as client:
        data = (await client.post(f"{API}/preview", files=csv_file("Code\nregular-77\n"), headers=admin)).json()["data"]
    assert data["rows"][0][2] == "Already Exists"


@pytest.mark.parametrize("text,fragment", [
    ("Code,Name\nABCD1,x\n", "more than one column"),
    ("Code\nABCD1,extra\n", "more than one column"),
    ("Code\n\n\n", "no codes"),
])
async def test_preview_refuses_files_that_are_not_one_column(admin, text, fragment):
    async with _client() as client:
        res = await client.post(f"{API}/preview", files=csv_file(text), headers=admin)
    assert res.status_code == 400 and fragment in res.json()["message"] and "One column only" in res.json()["message"]


async def test_preview_upload_limits_and_types(admin, monkeypatch):
    async with _client() as client:
        res = await client.post(f"{API}/preview", files=csv_file("Code\nABCD1\n", "x.png", "image/png"), headers=admin)
        assert res.status_code == 400
        res = await client.post(f"{API}/preview", files=csv_file("", "empty.csv"), headers=admin)
        assert res.status_code == 400
        res = await client.post(f"{API}/preview", files=csv_file("Code\ncafé\n".encode("latin-1")), headers=admin)
        assert res.status_code == 400 and "UTF-8" in res.json()["message"]
        # UTF-8 with BOM and CRLF is fine.
        res = await client.post(f"{API}/preview", files=csv_file("﻿Code\r\nABCD1\r\n"), headers=admin)
        assert res.status_code == 200
        monkeypatch.setattr(settings, "COUPON_BUNDLE_MAX_BYTES", 64)
        res = await client.post(f"{API}/preview", files=csv_file("Code\n" + "ABCDEFGH\n" * 20), headers=admin)
        assert res.status_code == 400 and "too large" in res.json()["message"]
        monkeypatch.setattr(settings, "COUPON_BUNDLE_MAX_BYTES", 2 * 1024 * 1024)
        monkeypatch.setattr(settings, "COUPON_BUNDLE_MAX_ROWS", 3)
        res = await client.post(f"{API}/preview", files=csv_file("Code\nAAAA1\nAAAA2\nAAAA3\nAAAA4\n"), headers=admin)
        assert res.status_code == 400 and "more than 3" in res.json()["message"]


# ------------------------------------------------------------------ import


async def test_import_normalises_dedupes_and_reports_a_summary(db, cleanup, admin):
    async with _client() as client:
        await create(client, admin, ["EXIST-0001"], cleanup)
        data = await create(client, admin, ["new-0001", " NEW-0001 ", "new-0002", "exist-0001", "x", "bad code", ""], cleanup)
    assert (data["added"], data["skipped_duplicates"], data["invalid"]) == (2, 2, 3)
    stored = {d["code"] async for d in db.coupon_codes.find({"bundle_id": data["bundle"]["id"]})}
    assert stored == {"NEW-0001", "NEW-0002"}
    assert data["bundle"]["counts"]["total"] == 2 and data["bundle"]["counts"]["unused"] == 2
    audit = await db.audit_logs.find_one({"action": "CREATE_COUPON_BUNDLE", "target_id": data["bundle"]["id"]})
    assert audit and audit["details"]["added"] == 2
    assert "NEW-0001" not in str(audit)  # codes never go into the audit trail


async def test_a_bundle_with_nothing_importable_is_not_created(db, admin):
    before = await db.coupon_bundles.count_documents({})
    async with _client() as client:
        res = await client.post(API, json=body(["a", "b c"]), headers=admin)
    assert res.status_code == 400
    assert await db.coupon_bundles.count_documents({}) == before


async def test_bundle_validation(db, cleanup, admin):
    async with _client() as client:
        assert (await client.post(API, json=body(["ABCD1"], valid_until=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()), headers=admin)).status_code == 422
        assert (await client.post(API, json=body(["ABCD1"], coupon_type="percentage", value=120), headers=admin)).status_code == 422
        assert (await client.post(API, json=body(["ABCD1"], value=0), headers=admin)).status_code == 422
        assert (await client.post(API, json=body(["ABCD1"], uses_per_coupon=0), headers=admin)).status_code == 422
        assert (await client.post(API, json=body([]), headers=admin)).status_code == 422
        ok = await create(client, admin, ["VAL-0001"], cleanup, valid_until=None, name="Dup Name")
        assert ok["bundle"]["valid_until"] is None and ok["bundle"]["state"] == "Active"
        assert (await client.post(API, json=body(["VAL-0002"], name="Dup Name"), headers=admin)).status_code == 409


async def test_add_more_codes_to_an_existing_bundle(db, cleanup, admin):
    async with _client() as client:
        made = await create(client, admin, ["ADD-0001", "ADD-0002"], cleanup)
        bid = made["bundle"]["id"]
        res = await client.post(f"{API}/{bid}/codes", json={"codes": ["add-0002", "ADD-0003"]}, headers=admin)
    data = res.json()["data"]
    assert (data["added"], data["skipped_duplicates"]) == (1, 1) and data["bundle"]["counts"]["total"] == 3


# ------------------------------------------------------------------ table: status / sort / search / pages


async def _use(db, code, uid, booking_id=None):
    svc = CouponService(db)
    coupon = await svc.repo.find_by_code(code)
    await svc.record_usage(str(coupon["_id"]), uid, booking_id or str(ObjectId()))


async def test_codes_table_status_filter_sort_search_and_pages(db, cleanup, admin):
    customer = await make_customer(db, name="Ravi Kumar")
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    other = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(other)}))
    cleanup.append(("coupon_user_usage", {"user_id": {"$in": [customer, other]}}))
    codes = [f"TBL-{i:04d}" for i in range(1, 9)]
    async with _client() as client:
        made = await create(client, admin, codes, cleanup, uses_per_coupon=2, usage_limit_per_user=1)
        bid = made["bundle"]["id"]
        await _use(db, "TBL-0001", customer)
        await _use(db, "TBL-0001", other)       # used (2 of 2)
        await _use(db, "TBL-0002", customer)    # partly used
        code3 = await db.coupon_codes.find_one({"code": "TBL-0003"})
        res = await client.put(f"{API}/{bid}/codes/{code3['_id']}", json={"is_active": False}, headers=admin)
        assert res.status_code == 200

        async def rows(**params):
            res = await client.get(f"{API}/{bid}/codes", params=params, headers=admin)
            assert res.status_code == 200, res.text
            return res.json()

        assert (await rows(status="used"))["meta"]["total"] == 1
        assert (await rows(status="partly_used"))["meta"]["total"] == 1
        assert (await rows(status="disabled"))["meta"]["total"] == 1
        assert (await rows(status="unused"))["meta"]["total"] == 5
        assert (await rows(status="expired"))["meta"]["total"] == 0
        everything = await rows(page_size=100)
        assert everything["meta"]["total"] == 8
        used_row = next(r for r in everything["data"] if r["code"] == "TBL-0001")
        assert used_row["status_label"] == "Used" and used_row["used_by"]["name"] and used_row["used_count"] == 2
        partly = next(r for r in everything["data"] if r["code"] == "TBL-0002")
        assert partly["status_label"] == "Partly Used" and partly["used_by"]["name"] == "Ravi Kumar" and partly["remaining"] == 1
        # Search by code (prefix, case-insensitive), pages, sort.
        assert [r["code"] for r in (await rows(search="tbl-000"))["data"]][:1]
        assert (await rows(search="TBL-0005"))["meta"]["total"] == 1
        page2 = await rows(page=2, page_size=3, sort_by="code", sort_order=1)
        assert [r["code"] for r in page2["data"]] == ["TBL-0004", "TBL-0005", "TBL-0006"] and page2["meta"]["total_pages"] == 3
        by_use = await rows(sort_by="used", sort_order=-1, status="partly_used")
        assert by_use["data"][0]["code"] == "TBL-0002"
        # Date filters.
        now = datetime.now(timezone.utc)
        assert (await rows(added_from=(now - timedelta(hours=1)).isoformat(), added_to=(now + timedelta(hours=1)).isoformat()))["meta"]["total"] == 8
        assert (await rows(added_from=(now + timedelta(hours=1)).isoformat()))["meta"]["total"] == 0
        assert (await rows(used_from=(now - timedelta(hours=1)).isoformat()))["meta"]["total"] == 2
        assert (await rows(used_to=(now - timedelta(hours=1)).isoformat()))["meta"]["total"] == 0
        assert (await client.get(f"{API}/{bid}/codes", params={"status": "bogus"}, headers=admin)).status_code == 400
        assert (await client.get(f"{API}/{bid}/codes", params={"sort_by": "bogus"}, headers=admin)).status_code == 422
        # Per-code history, and bundle counts.
        uses = (await client.get(f"{API}/{bid}/codes/{used_row['id']}/uses", headers=admin)).json()["data"]
        assert len(uses) == 2 and {u["user"]["id"] for u in uses} == {customer, other}
        detail = (await client.get(f"{API}/{bid}", headers=admin)).json()["data"]
        assert detail["counts"] == {"total": 8, "redemptions": 3, "unused": 5, "partly_used": 1, "used": 1, "expired": 0, "disabled": 1}
        listing = (await client.get(API, params={"search": made["bundle"]["name"][:8]}, headers=admin)).json()
        assert any(b["id"] == bid and b["counts"]["total"] == 8 for b in listing["data"])


async def test_an_expired_bundle_shows_its_unused_codes_as_expired(db, cleanup, admin):
    async with _client() as client:
        made = await create(client, admin, ["EXP-0001", "EXP-0002"], cleanup)
        bid = made["bundle"]["id"]
        who = await make_customer(db)
        cleanup += [("users", {"_id": ObjectId(who)}), ("coupon_user_usage", {"user_id": who})]
        await _use(db, "EXP-0001", who)
        await db.coupon_bundles.update_one({"_id": ObjectId(bid)}, {"$set": {"valid_until": datetime.now(timezone.utc) - timedelta(minutes=1)}})
        detail = (await client.get(f"{API}/{bid}", headers=admin)).json()["data"]
        assert detail["state"] == "Expired"
        assert (detail["counts"]["used"], detail["counts"]["expired"], detail["counts"]["unused"]) == (1, 1, 0)
        res = await client.get(f"{API}/{bid}/codes", params={"status": "expired"}, headers=admin)
        assert [r["code"] for r in res.json()["data"]] == ["EXP-0002"]


# ------------------------------------------------------------------ export


async def test_export_is_formula_safe_and_carries_status(db, cleanup, admin):
    customer = await make_customer(db, name="=HYPERLINK(\"http://evil\")")
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    cleanup.append(("coupon_user_usage", {"user_id": customer}))
    async with _client() as client:
        made = await create(client, admin, ["-LEAD-0001", "EXP-0001"], cleanup)
        bid = made["bundle"]["id"]
        await _use(db, "EXP-0001", customer)
        res = await client.get(f"{API}/{bid}/export.csv", params={"sort_by": "code", "sort_order": 1}, headers=admin)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"] and res.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows[0] == ["Code", "Status", "Times Used", "Uses Allowed", "Added On", "Last Used On", "Used By", "Phone", "Booking"]
    lead = next(r for r in rows if r[1] == "Unused")
    used = next(r for r in rows if r[1] == "Used")
    assert lead[0] == "'-LEAD-0001"  # formula-neutralised
    assert used[6].startswith("'=HYPERLINK")  # the customer's name too
    assert used[5].endswith(("AM", "PM"))  # 12-hour IST
    assert await db.audit_logs.find_one({"action": "EXPORT_COUPON_BUNDLE", "target_id": bid})


# ------------------------------------------------------------------ edit as one unit


async def test_editing_the_bundle_changes_every_code_without_touching_them(db, cleanup, admin):
    customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    async with _client() as client:
        made = await create(client, admin, [f"EDT-{i:04d}" for i in range(1, 6)], cleanup)
        bid = made["bundle"]["id"]
        stamp = {d["code"]: d for d in await db.coupon_codes.find({"bundle_id": bid}).to_list(None)}
        res = await client.put(f"{API}/{bid}", json={"name": "Renamed", "coupon_type": "percentage", "value": 50, "max_discount_amount": 80, "min_order_value": 200}, headers=admin)
        assert res.status_code == 200, res.text
        svc = CouponService(db)
        _c, discount = await svc.validate_and_compute_discount("edt-0003", 400, customer, use="services")
        assert discount == 80  # 50% of 400 = 200, capped at 80
        with pytest.raises(Exception, match="Minimum order"):
            await svc.validate_and_compute_discount("EDT-0005", 199, customer, use="services")
        # Codes themselves were not rewritten.
        after = {d["code"]: d for d in await db.coupon_codes.find({"bundle_id": bid}).to_list(None)}
        assert after == stamp
        audit = await db.audit_logs.find_one({"action": "UPDATE_COUPON_BUNDLE", "target_id": bid})
        assert audit["details"]["changes"]["value"] == {"before": 100, "after": 50}
        # Expiry: a past time expires the whole bundle at once; null clears it.
        res = await client.put(f"{API}/{bid}", json={"valid_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}, headers=admin)
        assert res.json()["data"]["state"] == "Expired"
        with pytest.raises(Exception, match="Expired"):
            await svc.validate_and_compute_discount("EDT-0001", 400, customer, use="services")
        res = await client.put(f"{API}/{bid}", json={"valid_until": None}, headers=admin)
        assert res.json()["data"]["valid_until"] is None
        await svc.validate_and_compute_discount("EDT-0001", 400, customer, use="services")
        # Switching it off hides every code at once; back on restores them.
        await client.put(f"{API}/{bid}", json={"is_active": False}, headers=admin)
        with pytest.raises(Exception, match="Invalid coupon code"):
            await svc.validate_and_compute_discount("EDT-0001", 400, customer, use="services")
        assert (await client.get(f"{API}/{bid}/codes", params={"status": "disabled"}, headers=admin)).json()["meta"]["total"] == 5
        await client.put(f"{API}/{bid}", json={"is_active": True}, headers=admin)
        await svc.validate_and_compute_discount("EDT-0001", 400, customer, use="services")
        # Bad shapes are refused.
        assert (await client.put(f"{API}/{bid}", json={"coupon_type": "percentage", "value": 150}, headers=admin)).status_code == 400
        assert (await client.put(f"{API}/{bid}", json={"uses_per_coupon": 0}, headers=admin)).status_code == 422
        other = await create(client, admin, ["EDT-9999"], cleanup)
        assert (await client.put(f"{API}/{bid}", json={"name": other["bundle"]["name"]}, headers=admin)).status_code == 409


async def test_uses_per_coupon_cannot_drop_below_what_is_already_used(db, cleanup, admin):
    users = [await make_customer(db) for _ in range(3)]
    cleanup += [("users", {"_id": ObjectId(u)}) for u in users] + [("coupon_user_usage", {"user_id": {"$in": users}})]
    async with _client() as client:
        made = await create(client, admin, ["USE-0001", "USE-0002", "USE-0003"], cleanup, uses_per_coupon=3)
        bid = made["bundle"]["id"]
        for u in users:
            await _use(db, "USE-0001", u)
        await _use(db, "USE-0002", users[0])
        res = await client.put(f"{API}/{bid}", json={"uses_per_coupon": 2}, headers=admin)
        assert res.status_code == 409
        assert "1 code has already been used more than 2 times" in res.json()["message"] and "3 or more" in res.json()["message"]
        assert (await client.get(f"{API}/{bid}", headers=admin)).json()["data"]["uses_per_coupon"] == 3
        # Raising it, or lowering to what the most-used code has had, is fine.
        assert (await client.put(f"{API}/{bid}", json={"uses_per_coupon": 5}, headers=admin)).status_code == 200
        assert (await client.put(f"{API}/{bid}", json={"uses_per_coupon": 3}, headers=admin)).status_code == 200
        detail = (await client.get(f"{API}/{bid}", headers=admin)).json()["data"]
        assert detail["counts"]["used"] == 1 and detail["counts"]["partly_used"] == 1
        # Raising the allowance turns a used code back into partly used.
        await client.put(f"{API}/{bid}", json={"uses_per_coupon": 4}, headers=admin)
        detail = (await client.get(f"{API}/{bid}", headers=admin)).json()["data"]
        assert detail["counts"]["used"] == 0 and detail["counts"]["partly_used"] == 2


async def test_disabling_one_code_blocks_only_that_code(db, cleanup, admin):
    customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    async with _client() as client:
        made = await create(client, admin, ["ONE-0001", "ONE-0002"], cleanup)
        bid = made["bundle"]["id"]
        code = await db.coupon_codes.find_one({"code": "ONE-0001"})
        await client.put(f"{API}/{bid}/codes/{code['_id']}", json={"is_active": False}, headers=admin)
        svc = CouponService(db)
        with pytest.raises(Exception, match="Invalid coupon code"):
            await svc.validate_and_compute_discount("ONE-0001", 400, customer, use="services")
        await svc.validate_and_compute_discount("ONE-0002", 400, customer, use="services")
        await client.put(f"{API}/{bid}/codes/{code['_id']}", json={"is_active": True}, headers=admin)
        await svc.validate_and_compute_discount("ONE-0001", 400, customer, use="services")
        assert await db.audit_logs.find_one({"action": "DISABLE_COUPON_CODE", "target_id": bid})
        # A code from another bundle can't be reached through this one.
        res = await client.put(f"{API}/{bid}/codes/{ObjectId()}", json={"is_active": False}, headers=admin)
        assert res.status_code == 404


# ------------------------------------------------------------------ delete


async def test_delete_removes_an_unused_bundle_and_only_switches_off_a_used_one(db, cleanup, admin):
    customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    cleanup.append(("coupon_user_usage", {"user_id": customer}))
    async with _client() as client:
        unused = await create(client, admin, ["DEL-0001", "DEL-0002"], cleanup)
        res = await client.delete(f"{API}/{unused['bundle']['id']}", headers=admin)
        assert res.status_code == 200 and res.json()["data"] == {"mode": "deleted", "codes_removed": 2}
        assert await db.coupon_codes.count_documents({"code": {"$in": ["DEL-0001", "DEL-0002"]}}) == 0
        assert (await client.get(f"{API}/{unused['bundle']['id']}", headers=admin)).status_code == 404
        # The freed codes can be imported again.
        again = await create(client, admin, ["DEL-0001"], cleanup)
        assert again["added"] == 1

        used = await create(client, admin, ["KEEP-0001", "KEEP-0002"], cleanup)
        bid = used["bundle"]["id"]
        await _use(db, "KEEP-0001", customer)
        res = await client.delete(f"{API}/{bid}", headers=admin)
        assert res.json()["data"]["mode"] == "disabled" and "history is kept" in res.json()["message"]
        # Hidden from the list and detail, codes stay (history), and refuse use.
        assert (await client.get(f"{API}/{bid}", headers=admin)).status_code == 404
        assert bid not in [b["id"] for b in (await client.get(API, headers=admin)).json()["data"]]
        assert await db.coupon_codes.count_documents({"bundle_id": bid, "code": "KEEP-0001"}) == 1
        with pytest.raises(Exception, match="Invalid coupon code"):
            await CouponService(db).validate_and_compute_discount("KEEP-0002", 400, customer, use="services")
        # ...but a booking that holds the used code can still give it back.
        await CouponService(db).reverse_usage("KEEP-0001", customer, (await db.coupon_usages.find_one({"bundle_id": bid}))["booking_id"])
        assert (await db.coupon_codes.find_one({"code": "KEEP-0001"}))["used_count"] == 0
        # Used codes still block a new import of the same code.
        res = await client.post(API, json=body(["KEEP-0001"]), headers=admin)
        assert res.status_code == 400
    actions = {a["action"] async for a in db.audit_logs.find({"target_id": bid})}
    assert "DISABLE_COUPON_BUNDLE" in actions


# ------------------------------------------------------------------ scale


async def test_a_hundred_thousand_codes_import_page_and_export_within_bounds(db, cleanup, admin):
    codes = [f"BULK-{i:06d}" for i in range(100_000)]
    async with _client() as client:
        started = time.monotonic()
        made = await create(client, admin, codes, cleanup)
        imported_in = time.monotonic() - started
        bid = made["bundle"]["id"]
        assert made["added"] == 100_000 and made["bundle"]["counts"]["total"] == 100_000
        assert imported_in < 120
        started = time.monotonic()
        last = await client.get(f"{API}/{bid}/codes", params={"page": 1000, "page_size": 100, "sort_by": "code", "sort_order": 1}, headers=admin)
        assert last.json()["data"][-1]["code"] == "BULK-099999" and last.json()["meta"]["total"] == 100_000
        unused = await client.get(f"{API}/{bid}/codes", params={"status": "unused", "page_size": 50}, headers=admin)
        assert unused.json()["meta"]["total"] == 100_000
        assert time.monotonic() - started < 15
        # Streams, never builds the file in memory: count the lines.
        lines = 0
        async with client.stream("GET", f"{API}/{bid}/export.csv", headers=admin) as stream:
            async for chunk in stream.aiter_lines():
                lines += 1
        assert lines == 100_001
        # Re-importing the same 100k is all duplicates.
        again = (await client.post(f"{API}/{bid}/codes", json={"codes": codes}, headers=admin)).json()["data"]
        assert (again["added"], again["skipped_duplicates"]) == (0, 100_000)
