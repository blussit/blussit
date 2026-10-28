"""
KYC documents: private by construction, authorized per request, audited,
immutable once verified, never lost with an account. End to end through the
real HTTP app with the local storage provider (no live services).
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from tests.factories import make_captain, make_customer, make_manager, make_service_center, own_upload_url

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\nkyc privacy test"
LOCK = "Verified Documents Can't Be Changed — Contact Your Manager"


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "public"))
    monkeypatch.setattr(settings, "KYC_PRIVATE_DIR", str(tmp_path / "private"))
    return tmp_path


@pytest.fixture
async def rig(db, cleanup, local_store):
    ca = await make_service_center(db, pincode="452781")
    cb = await make_service_center(db, pincode="452782")
    m1, m2 = await make_manager(db, ca), await make_manager(db, cb)
    cap_a, cap_b = await make_captain(db, ca, wallet_balance=0), await make_captain(db, ca, wallet_balance=0)
    cust = await make_customer(db)
    users = [m1, m2, cap_a, cap_b, cust]
    cleanup.extend([
        ("service_centers", {"_id": {"$in": [ObjectId(ca), ObjectId(cb)]}}),
        ("captain_wallets", {"captain_id": {"$in": [cap_a, cap_b]}}),
        ("uploaded_documents", {"owner_id": {"$in": users}}),
        ("upload_counters", {"_id": {"$regex": "|".join(users)}}),
        ("document_download_counters", {"_id": {"$regex": "|".join(users)}}),
        ("kyc_versions", {"captain_id": {"$in": users}}),
        ("kyc_retained", {"captain_id": {"$in": users}}),
        ("notifications", {"user_id": {"$in": users}}),
        ("audit_logs", {"$or": [{"actor_id": {"$in": users}}, {"target_id": {"$in": users}}]}),
        ("users", {"_id": {"$in": [ObjectId(u) for u in users]}}),
    ])
    admin = str((await db.users.find_one({"role": "admin"}))["_id"])
    return {
        "db": db, "ca": ca, "cb": cb, "m1": _auth(m1, "manager", ca), "m2": _auth(m2, "manager", cb),
        "unlinked": _auth(await _unlinked(db, cleanup), "manager"), "a": _auth(cap_a, "captain", ca),
        "b": _auth(cap_b, "captain", ca), "cust": _auth(cust, "customer"), "admin": _auth(admin, "admin"),
        "cap_a": cap_a, "cap_b": cap_b, "admin_id": admin, "m1_id": m1,
    }


async def _unlinked(db, cleanup):
    uid = await make_manager(db, None)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    return uid


async def _upload(client, headers, data=PNG, ctype="image/png"):
    res = await client.post("/api/v1/uploads/document", files={"file": ("x", data, ctype)}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]["url"]


def _path(url: str) -> str:
    return "/api/v1" + url.split("/api/v1", 1)[1]


async def _submit(client, headers, aadhaar, pan):
    return await client.put("/api/v1/staff/my-kyc", headers=headers, json={
        "photo_url": own_upload_url("photos/me.jpg"), "aadhaar_number": "123412341234", "pan_number": "ABCDE1234F",
        "aadhaar_doc_url": aadhaar, "pan_doc_url": pan, "local_address": "1 Lane, Indore", "same_as_local": True,
    })


# ----------------------------------------------------------- private storage


async def test_documents_are_stored_privately_and_never_served_statically(rig, local_store):
    async with _client() as c:
        url = await _upload(c, rig["a"])
        key = url.split("/uploads/document/", 1)[1]
        # Not under the public uploads dir; on disk in the private one only.
        assert (local_store / "private" / key).is_file()
        assert not (local_store / "public" / key).exists()
        assert "/api/v1/uploads/document/documents/" in url and "/uploads/documents/" not in url
        name = key.split("/", 1)[1]
        # A copy planted in the legacy public folder is still never served statically.
        (local_store / "public" / "documents").mkdir(parents=True)
        (local_store / "public" / "documents" / name).write_bytes(PNG)
        assert (await c.get(f"/uploads/documents/{name}")).status_code == 404
        assert (await c.head(f"/uploads/documents/{name}")).status_code in (404, 405)
        # Random, unguessable key: 32 hex chars, not an id.
        assert len(name.split(".")[0]) == 32
        assert rig["cap_a"] not in key


async def test_served_type_comes_from_the_bytes_not_the_client(rig):
    async with _client() as c:
        url = await _upload(c, rig["a"], data=PNG, ctype="image/jpeg")  # lies about its type
        assert url.endswith(".png")
        res = await c.get(_path(url), headers=rig["a"])
        assert res.headers["content-type"] == "image/png"
        # A script labelled as an image is refused outright.
        bad = await c.post("/api/v1/uploads/document", files={"file": ("x", b"<script>alert(1)</script>", "image/png")}, headers=rig["a"])
        assert bad.status_code == 400


async def test_download_headers_are_locked_down(rig):
    async with _client() as c:
        url = await _upload(c, rig["a"], PDF, "application/pdf")
        res = await c.get(_path(url), headers={**rig["a"], "Origin": "https://evil.example", "Range": "bytes=0-5"})
    assert res.status_code == 200 and res.content == PDF, "Range is ignored: always the full body, never a partial"
    h = res.headers
    assert h["cache-control"].startswith("private, no-store")
    assert h["x-content-type-options"] == "nosniff"
    assert h["content-security-policy"] == "default-src 'none'; sandbox"
    assert h["content-disposition"] == 'inline; filename="document.pdf"'
    assert h["content-type"] == "application/pdf" and h["accept-ranges"] == "none"
    assert "access-control-allow-origin" not in h, "an unknown origin gets no CORS grant"


# ------------------------------------------------------------ authorization


async def test_idor_matrix(rig):
    async with _client() as c:
        url = await _upload(c, rig["a"])
        p = _path(url)
        got = {
            "owner": (await c.get(p, headers=rig["a"])).status_code,
            "other_captain": (await c.get(p, headers=rig["b"])).status_code,
            "manager_same_center": (await c.get(p, headers=rig["m1"])).status_code,
            "manager_other_center": (await c.get(p, headers=rig["m2"])).status_code,
            "manager_no_center": (await c.get(p, headers=rig["unlinked"])).status_code,
            "customer": (await c.get(p, headers=rig["cust"])).status_code,
            "admin": (await c.get(p, headers=rig["admin"])).status_code,
            "anonymous": (await c.get(p)).status_code,
        }
    assert got == {
        "owner": 200, "other_captain": 403, "manager_same_center": 200, "manager_other_center": 403,
        "manager_no_center": 403, "customer": 403, "admin": 200, "anonymous": 401,
    }


async def test_path_traversal_guessing_and_method_abuse(rig):
    async with _client() as c:
        url = await _upload(c, rig["a"])
        real = url.split("/uploads/document/", 1)[1]
        name = real.split("/", 1)[1]
        base = "/api/v1/uploads/document/"
        for bad in (
            "documents/../../.env", "documents/%2e%2e/%2e%2e/etc/passwd", "..%2F..%2Fetc%2Fpasswd", "photos/" + name,
            "documents/" + "a" * 32 + ".png", "documents/" + name.replace(".png", ".html"),
            "documents/x.svg", "documents//" + name, "documents\\" + name,
        ):
            res = await c.get(base + bad, headers=rig["admin"])
            assert res.status_code in (404, 400), (bad, res.status_code)
        # Writing/deleting through the document route is not possible.
        for method in ("delete", "put", "post", "patch", "head"):
            res = await getattr(c, method)(base + real, headers=rig["admin"])
            assert res.status_code in (404, 405), (method, res.status_code)
        assert (await c.get(base + real, headers=rig["a"])).status_code == 200, "still there after the abuse"


async def test_non_owner_views_and_refusals_are_audited_without_leaking(rig):
    db = rig["db"]
    async with _client() as c:
        url = await _upload(c, rig["a"])
        p = _path(url)
        await c.get(p, headers=rig["a"])  # owner: not audited
        await c.get(p, headers=rig["m1"])
        await c.get(p, headers=rig["admin"])
        await c.get(p, headers=rig["b"])  # refused: audited
        logs = await c.get("/api/v1/audit-logs", params={"module": "kyc"}, headers=rig["admin"])
        forbidden = await c.get("/api/v1/audit-logs", params={"module": "kyc"}, headers=rig["m1"])
    rows = await db.audit_logs.find({"module": "kyc", "target_id": rig["cap_a"]}).to_list(length=20)
    actions = sorted((r["action"], r["actor_role"]) for r in rows)
    assert actions == [("KYC_DOCUMENT_ACCESS_DENIED", "captain"), ("VIEW_KYC_DOCUMENT", "admin"), ("VIEW_KYC_DOCUMENT", "manager")]
    for r in rows:
        assert r["details"]["ip"] and r["details"]["key"].startswith("documents/")
        assert url not in str(r) and "123412341234" not in str(r)
    assert logs.status_code == 200 and logs.json()["pagination"]["total"] >= 3 if "pagination" in logs.json() else logs.status_code == 200
    assert forbidden.status_code == 403, "the audit view is admin-only"


async def test_downloads_are_rate_limited_per_user(rig, monkeypatch):
    from app.routes.v1 import upload_routes

    monkeypatch.setattr(upload_routes, "DOC_DOWNLOADS_PER_WINDOW", 3)
    async with _client() as c:
        url = await _upload(c, rig["a"])
        codes = [(await c.get(_path(url), headers=rig["a"])).status_code for _ in range(5)]
        other = (await c.get(_path(url), headers=rig["m1"])).status_code
    assert codes == [200, 200, 200, 429, 429] and other == 200


# --------------------------------------------------------------- masking


async def test_manager_sees_last_four_only_admin_sees_full_and_lists_have_no_numbers(rig):
    async with _client() as c:
        a, p = await _upload(c, rig["a"]), await _upload(c, rig["a"])
        assert (await _submit(c, rig["a"], a, p)).status_code == 200
        mine = (await c.get("/api/v1/staff/my-kyc", headers=rig["a"])).json()["data"]
        mgr = (await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc", headers=rig["m1"])).json()["data"]
        adm = (await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc", headers=rig["admin"])).json()["data"]
        other = await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc", headers=rig["m2"])
        peer = await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc", headers=rig["b"])
        roster = await c.get(f"/api/v1/staff/captains/center/{rig['ca']}", headers=rig["m1"])
    assert mine["aadhaar_number"] == "123412341234"
    assert mgr["aadhaar_number"].endswith("1234") and "123412341234" not in str(mgr) and mgr["pan_number"].endswith("234F")
    assert adm["aadhaar_number"] == "123412341234"
    assert other.status_code == 403 and peer.status_code == 403
    assert "123412341234" not in roster.text and "ABCDE1234F" not in roster.text and "aadhaar" not in roster.text.lower()


# ------------------------------------------------------------- immutability


async def test_verified_documents_cannot_be_changed_by_the_captain(rig):
    db = rig["db"]
    async with _client() as c:
        a, p = await _upload(c, rig["a"]), await _upload(c, rig["a"])
        assert (await _submit(c, rig["a"], a, p)).status_code == 200
        ok = await c.post(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/review", json={"status": "verified"}, headers=rig["m1"])
        assert ok.status_code == 200
        # Captain: edit, replace, new upload — all refused with the clear message.
        edit = await _submit(c, rig["a"], a, p)
        new_doc = await c.post("/api/v1/uploads/document", files={"file": ("x", PNG, "image/png")}, headers=rig["a"])
        newer = await _submit(c, rig["a"], await _upload(c, rig["b"]), p)  # also a foreign doc
        for res in (edit, new_doc):
            assert res.status_code == 409 and res.json()["message"] == LOCK
        assert newer.status_code in (400, 409)
        # No captain-facing way to delete: the only KYC routes are get/put.
        for verb in ("delete", "patch"):
            assert (await getattr(c, verb)("/api/v1/staff/my-kyc", headers=rig["a"])).status_code in (404, 405)
        # The verified files are still readable.
        assert (await c.get(_path(a), headers=rig["a"])).status_code == 200
        # A manager of another center cannot reopen it; the owner's manager must give a reason.
        assert (await c.post(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/request-reverification", json={"note": "recheck pan"}, headers=rig["m2"])).status_code == 403
        assert (await c.post(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/request-reverification", json={"note": ""}, headers=rig["m1"])).status_code == 422
        assert (await c.post(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/request-reverification", json={"note": "Please recheck PAN"}, headers=rig["m1"])).status_code == 200
        # Reopened: the captain may resubmit (a NEW packet), the verified one is kept as version 1.
        a2 = await _upload(c, rig["a"])
        assert (await _submit(c, rig["a"], a2, p)).status_code == 200
        versions = (await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/versions", headers=rig["m1"])).json()["data"]
        deny = await c.get(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/versions", headers=rig["m2"])
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["packet"]["status"] == "verified" and versions[0]["reason"] == "reverification_requested"
    assert "123412341234" not in str(versions) and versions[0]["packet"]["aadhaar_number"].endswith("1234")
    assert deny.status_code == 403
    # Old files still exist — nothing was deleted.
    assert await db.uploaded_documents.count_documents({"owner_id": rig["cap_a"]}) == 3


# ------------------------------------------------------- deleting an account


async def test_deleting_a_captain_keeps_their_kyc_and_files_admin_only(rig, local_store):
    from app.services.user_service import UserService

    db = rig["db"]
    async with _client() as c:
        a, p = await _upload(c, rig["a"]), await _upload(c, rig["a"])
        await _submit(c, rig["a"], a, p)
        await c.post(f"/api/v1/staff/captains/{rig['cap_a']}/kyc/review", json={"status": "verified"}, headers=rig["m1"])
        assert await UserService(db).delete_user(rig["cap_a"]) is True
        retained = await db.kyc_retained.find_one({"captain_id": rig["cap_a"]})
        assert retained and retained["captain_kyc"]["status"] == "verified"
        assert (local_store / "private" / a.split("/uploads/document/", 1)[1]).is_file(), "files survive the account"
        # Only an admin can still open it; the old manager can't.
        assert (await c.get(_path(a), headers=rig["admin"])).status_code == 200
        assert (await c.get(_path(a), headers=rig["m1"])).status_code == 403
        assert (await c.get(_path(a), headers=rig["b"])).status_code == 403


# ---------------------------------------------------------------- migration


async def test_legacy_public_local_documents_are_moved_and_urls_rewritten(rig, local_store):
    from app.core import kyc_migration
    from app.core.storage import _local_upload_base

    db = rig["db"]
    legacy = local_store / "public" / "documents"
    legacy.mkdir(parents=True)
    (legacy / "abc123.png").write_bytes(PNG)
    old_url = f"{_local_upload_base()}documents/abc123.png"
    await db.users.update_one({"_id": ObjectId(rig["cap_a"])}, {"$set": {"captain_kyc": {"aadhaar_doc_url": old_url, "status": "verified"}}})
    assert kyc_migration.move_local_documents() == 1
    assert not (legacy / "abc123.png").exists() and (local_store / "private" / "documents" / "abc123.png").is_file()
    assert await kyc_migration.rewrite_local_document_urls(db) == 1
    assert await kyc_migration.rewrite_local_document_urls(db) == 0, "idempotent"
    new = (await db.users.find_one({"_id": ObjectId(rig["cap_a"])}))["captain_kyc"]["aadhaar_doc_url"]
    assert "/api/v1/uploads/document/documents/abc123.png" in new
    await db.uploaded_documents.insert_one({"key": "documents/abc123.png", "url": new, "owner_id": rig["cap_a"], "created_at": datetime.now(timezone.utc)})
    async with _client() as c:
        assert (await c.get(_path(new), headers=rig["a"])).status_code == 200
        assert (await c.get(_path(new), headers=rig["b"])).status_code == 403
