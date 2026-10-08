"""
Hardening batch (2026-10 audit): behavioural regressions for each fix.

- CRM 360 / phone search: a manager reaches only customers their center has
  dealt with (404 otherwise, unlinked manager fails closed); phone search
  gives a manager the minimal row only.
- Public catalogue: GET /reviews shows ratings/words only; captain_fee and
  switched-off rows (active_only=false) are for admins/managers.
- GET /coupons/public/{code}: customer-facing terms only, own tight bucket.
- WhatsApp webhook: unsigned events refused outside a development run.
- Uploads: staff only, per-day cap, document ownership drives KYC submit
  and download authorization; photo/KYC URLs must be our own storage.
- Leave requests start "pending" (manager list + review work).
- Bank details validated + audited; withdrawals one-pending-at-a-time even
  in parallel; NaN money refused with a clean 422.
- Pagination bound, rate-limit buckets (IPv6 /64, staff writes, new public
  buckets), request-body ceiling (413), business-settings types, center
  scoping on GET /service-centers/{id} and a minimal public pincode lookup.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pydantic
import pytest
from bson import ObjectId
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from tests.factories import make_captain, make_customer, make_manager, make_service_center, own_upload_url

pytestmark = pytest.mark.asyncio

PDF = b"%PDF-1.7\nhardening test document"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


def _client():
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def rig(db, cleanup):
    center_a = await make_service_center(db, pincode="452771")
    center_b = await make_service_center(db, pincode="452772")
    manager_a = await make_manager(db, center_a)
    manager_b = await make_manager(db, center_b)
    unlinked = await make_manager(db, None)
    captain_a = await make_captain(db, center_a)
    captain_b = await make_captain(db, center_a)
    known = await make_customer(db)
    stranger = await make_customer(db)
    users = [manager_a, manager_b, unlinked, captain_a, captain_b, known, stranger]
    now = datetime.now(timezone.utc)
    booking = await db.bookings.insert_one({
        "booking_number": f"BK-HARD-{ObjectId()}", "customer_id": known, "service_center_id": center_a,
        "status": "completed", "total_amount": 300, "scheduled_date": now, "created_at": now, "is_deleted": False,
    })
    cleanup.extend([
        ("bookings", {"_id": booking.inserted_id}),
        ("service_centers", {"_id": {"$in": [ObjectId(center_a), ObjectId(center_b)]}}),
        ("captain_wallets", {"captain_id": {"$in": [captain_a, captain_b]}}),
        ("uploaded_documents", {"owner_id": {"$in": users}}),
        ("upload_counters", {"_id": {"$regex": "|".join(users)}}),
        ("leave_requests", {"captain_id": {"$in": [captain_a, captain_b]}}),
        ("withdrawal_requests", {"captain_id": {"$in": [captain_a, captain_b]}}),
        ("wallet_transactions", {"captain_id": {"$in": [captain_a, captain_b]}}),
        ("notifications", {"user_id": {"$in": users}}),
        ("audit_logs", {"actor_id": {"$in": users}}),
        ("users", {"_id": {"$in": [ObjectId(u) for u in users]}}),
    ])
    admin = await db.users.find_one({"role": "admin"})
    return {
        "db": db, "center_a": center_a, "center_b": center_b, "manager_a": manager_a, "manager_b": manager_b,
        "unlinked": unlinked, "captain_a": captain_a, "captain_b": captain_b, "known": known, "stranger": stranger,
        "admin": str(admin["_id"]),
    }


# ---------------------------------------------------------------- 1. CRM


async def test_manager_customer_360_requires_a_relationship_with_their_center(rig):
    async with _client() as client:
        mine = await client.get(f"/api/v1/crm/customers/{rig['known']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        stranger = await client.get(f"/api/v1/crm/customers/{rig['stranger']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        other_center = await client.get(f"/api/v1/crm/customers/{rig['known']}", headers=_auth(rig["manager_b"], "manager", rig["center_b"]))
        unlinked = await client.get(f"/api/v1/crm/customers/{rig['known']}", headers=_auth(rig["unlinked"], "manager"))
        admin = await client.get(f"/api/v1/crm/customers/{rig['stranger']}", headers=_auth(rig["admin"], "admin"))
    assert mine.status_code == 200 and mine.json()["data"]["profile"]["id"] == rig["known"]
    # No profile, addresses or vehicles of a customer the center never served.
    assert stranger.status_code == 404 and other_center.status_code == 404
    assert unlinked.status_code == 404, "a manager with no center fails closed"
    assert admin.status_code == 200


async def test_phone_search_gives_a_manager_the_minimal_row_only(rig):
    phone = (await rig["db"].users.find_one({"_id": ObjectId(rig["stranger"])}))["phone"]
    async with _client() as client:
        manager = await client.get("/api/v1/crm/customers/search", params={"phone": phone}, headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        unlinked = await client.get("/api/v1/crm/customers/search", params={"phone": phone}, headers=_auth(rig["unlinked"], "manager"))
        admin = await client.get("/api/v1/crm/customers/search", params={"phone": phone}, headers=_auth(rig["admin"], "admin"))
    row = manager.json()["data"]
    assert set(row) == {"id", "full_name", "phone", "role"} and row["id"] == rig["stranger"]
    assert unlinked.json()["data"] is None
    assert admin.json()["data"]["email"], "admin keeps the full profile"


# ------------------------------------------------- 2. public catalogue


async def test_public_reviews_show_ratings_and_words_only(rig, cleanup):
    review = await rig["db"].reviews.insert_one({
        "booking_id": str(ObjectId()), "customer_id": rig["known"], "captain_id": rig["captain_a"],
        "service_center_id": rig["center_a"], "service_rating": 5, "service_comment": "Spotless",
        "original_review": {"service_comment": "first draft"}, "is_published": True, "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
    })
    cleanup.append(("reviews", {"_id": review.inserted_id}))
    async with _client() as client:
        res = await client.get("/api/v1/reviews", params={"page_size": 100})
    assert res.status_code == 200
    rows = res.json()["data"]
    mine = next(r for r in rows if r["id"] == str(review.inserted_id))
    assert mine["service_comment"] == "Spotless"
    for row in rows:
        assert not {"customer_id", "captain_id", "booking_id", "service_center_id", "original_review", "deleted_by"} & set(row)


async def test_captain_fee_and_switched_off_rows_are_for_catalogue_editors(rig, cleanup):
    db = rig["db"]
    category = await db.categories.find_one({"is_deleted": {"$ne": True}})
    hidden = await db.services.insert_one({
        "category_id": str(category["_id"]), "name": "Hardening Hidden Service", "slug": f"hardening-hidden-{ObjectId()}",
        "price": 100.0, "captain_fee": 55.0, "is_active": False, "is_deleted": False, "created_at": datetime.now(timezone.utc),
    })
    live = await db.services.insert_one({
        "category_id": str(category["_id"]), "name": "Hardening Live Service", "slug": f"hardening-live-{ObjectId()}",
        "price": 100.0, "captain_fee": 44.0, "is_active": True, "is_deleted": False, "created_at": datetime.now(timezone.utc),
    })
    vtype = await db.vehicle_types.insert_one({"name": "Hardening Hidden Type", "slug": f"hardening-{ObjectId()}", "is_active": False, "is_deleted": False})
    faq = await db.faqs.insert_one({"question": "Hardening hidden FAQ?", "answer": "x", "is_active": False, "is_deleted": False})
    cleanup.extend([
        ("services", {"_id": {"$in": [hidden.inserted_id, live.inserted_id]}}),
        ("vehicle_types", {"_id": vtype.inserted_id}),
        ("faqs", {"_id": faq.inserted_id}),
    ])
    hidden_id, live_id = str(hidden.inserted_id), str(live.inserted_id)
    all_rows = {"active_only": "false", "page_size": 100}

    async with _client() as client:
        anon = (await client.get("/api/v1/services", params=all_rows)).json()["data"]
        customer = (await client.get("/api/v1/services", params=all_rows, headers=_auth(rig["known"], "customer"))).json()["data"]
        admin = (await client.get("/api/v1/services", params=all_rows, headers=_auth(rig["admin"], "admin"))).json()["data"]
        one_anon = (await client.get(f"/api/v1/services/{live_id}")).json()["data"]
        stale = await client.get("/api/v1/services", params=all_rows, headers={"Authorization": "Bearer not-a-token"})
        stale_public = await client.get("/api/v1/services", headers={"Authorization": "Bearer not-a-token"})
        types_anon = (await client.get("/api/v1/vehicle-types", params={"active_only": "false"})).json()["data"]
        types_admin = (await client.get("/api/v1/vehicle-types", params={"active_only": "false"}, headers=_auth(rig["admin"], "admin"))).json()["data"]
        faqs_anon = (await client.get("/api/v1/faqs", params={"active_only": "false"})).json()["data"]

    for rows in (anon, customer):
        ids = {s["id"] for s in rows}
        assert hidden_id not in ids and live_id in ids
        assert all("captain_fee" not in s for s in rows)
    assert "captain_fee" not in one_anon
    by_id = {s["id"]: s for s in admin}
    assert by_id[hidden_id]["captain_fee"] == 55.0 and by_id[live_id]["captain_fee"] == 44.0
    # An admin screen with an expired session gets a 401 (so the app refreshes
    # and retries) rather than a silent public view; a public page doesn't.
    assert stale.status_code == 401 and stale_public.status_code == 200
    assert str(vtype.inserted_id) not in {t["id"] for t in types_anon}
    assert str(vtype.inserted_id) in {t["id"] for t in types_admin}
    assert str(faq.inserted_id) not in {f["id"] for f in faqs_anon}


# ------------------------------------------------------------ 3. coupons


async def test_public_offer_lookup_returns_customer_terms_only(rig, cleanup):
    code = f"HARD{str(ObjectId())[-6:].upper()}"
    now = datetime.now(timezone.utc)
    await rig["db"].coupons.insert_one({
        "code": code, "description": "Test offer", "coupon_type": "flat", "value": 50.0, "min_order_value": 0,
        "usage_limit_per_user": 3, "total_usage_limit": 100, "total_used": 97, "created_by": rig["admin"],
        "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=1), "is_active": True, "is_deleted": False,
    })
    cleanup.append(("coupons", {"code": code}))
    async with _client() as client:
        res = await client.get(f"/api/v1/coupons/public/{code}")
    assert res.status_code == 200
    offer = res.json()["data"]
    assert offer["code"] == code and offer["value"] == 50.0
    assert not {"total_used", "total_usage_limit", "usage_limit_per_user", "created_by"} & set(offer)


# ------------------------------------------------------- 4. WhatsApp webhook


async def test_unsigned_webhook_is_refused_outside_development(monkeypatch, db):
    from app.core.config import settings
    from app.core.exceptions import ForbiddenException
    from app.routes.v1.whatsapp_webhook_routes import verify_webhook_signature, verify_webhook_subscription

    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert verify_webhook_signature(b"{}", None) is False, "DEBUG on in production must not open the webhook"
    async with _client() as client:
        res = await client.post("/api/v1/whatsapp/webhook", content=b'{"entry": []}', headers={"Content-Type": "application/json"})
    assert res.status_code == 403
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert verify_webhook_signature(b"{}", None) is True

    monkeypatch.setattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "verify-me")
    assert verify_webhook_subscription("subscribe", "verify-me", "c") == "c"
    for bad in (None, "", "verify-mf", "verify-me-too"):
        with pytest.raises(ForbiddenException):
            verify_webhook_subscription("subscribe", bad, "c")


# ------------------------------------------------------------- 5/6. uploads


@pytest.fixture
def local_uploads(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


async def test_uploads_are_staff_only_and_capped_per_day(rig, local_uploads, monkeypatch):
    from app.controllers import upload_controller

    async with _client() as client:
        refused = await client.post("/api/v1/uploads/photo", files={"file": ("a.png", PNG, "image/png")}, headers=_auth(rig["known"], "customer"))
        assert refused.status_code == 403
        monkeypatch.setattr(upload_controller, "DAILY_UPLOAD_LIMIT", 2)
        codes = [
            (await client.post("/api/v1/uploads/photo", files={"file": ("a.png", PNG, "image/png")}, headers=_auth(rig["captain_a"], "captain", rig["center_a"]))).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]


async def test_kyc_may_only_name_the_captains_own_documents(rig, local_uploads):
    captain_a = _auth(rig["captain_a"], "captain", rig["center_a"])
    captain_b = _auth(rig["captain_b"], "captain", rig["center_a"])
    async with _client() as client:
        aadhaar = (await client.post("/api/v1/uploads/document", files={"file": ("a.pdf", PDF, "application/pdf")}, headers=captain_a)).json()["data"]["url"]
        pan = (await client.post("/api/v1/uploads/document", files={"file": ("p.pdf", PDF, "application/pdf")}, headers=captain_a)).json()["data"]["url"]
        record = await rig["db"].uploaded_documents.find_one({"url": aadhaar})
        assert record and record["owner_id"] == rig["captain_a"] and record["key"].startswith("documents/")

        packet = {
            "photo_url": own_upload_url("photos/me.jpg"), "aadhaar_number": "123412341234", "pan_number": "ABCDE1234F",
            "aadhaar_doc_url": aadhaar, "pan_doc_url": pan, "local_address": "1 Test Lane, Indore", "same_as_local": True,
        }
        stolen = await client.put("/api/v1/staff/my-kyc", json=packet, headers=captain_b)
        foreign = await client.put("/api/v1/staff/my-kyc", json={**packet, "photo_url": "https://evil.example/x.jpg"}, headers=captain_a)
        too_long = await client.put("/api/v1/staff/my-kyc", json={**packet, "photo_url": own_upload_url("photos/" + "a" * 600)}, headers=captain_a)
        own = await client.put("/api/v1/staff/my-kyc", json=packet, headers=captain_a)
    assert stolen.status_code == 400, "captain B named captain A's Aadhaar"
    assert foreign.status_code == 422 and too_long.status_code == 422
    assert own.status_code == 200 and own.json()["data"]["status"] == "submitted"


async def test_document_download_is_authorized_by_the_uploader(rig, monkeypatch):
    from app.core.config import settings
    from app.routes.v1 import upload_routes

    async def fake_download(key):
        return b"%PDF-1.7 secret", "application/pdf"

    monkeypatch.setattr(upload_routes, "download_private_document", fake_download)
    db = rig["db"]
    key = f"documents/{ObjectId()}.pdf"
    url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/uploads/document/{key}"
    await db.uploaded_documents.insert_one({"key": key, "url": url, "owner_id": rig["captain_a"]})
    # Captain B planted A's URL in their own packet (the pre-fix attack).
    await db.users.update_one({"_id": ObjectId(rig["captain_b"])}, {"$set": {"captain_kyc": {"aadhaar_doc_url": url}}})
    legacy_key = f"documents/{ObjectId()}.pdf"
    legacy_url = url.replace(key, legacy_key)
    for captain in (rig["captain_a"], rig["captain_b"]):  # no record + two packets naming it
        await db.users.update_one({"_id": ObjectId(captain)}, {"$set": {"captain_kyc.pan_doc_url": legacy_url}})

    async def get(path_key, user, role, center=None):
        async with _client() as client:
            return (await client.get(f"/api/v1/uploads/document/{path_key}", headers=_auth(user, role, center))).status_code

    assert await get(key, rig["captain_a"], "captain", rig["center_a"]) == 200
    assert await get(key, rig["captain_b"], "captain", rig["center_a"]) == 403
    assert await get(key, rig["manager_a"], "manager", rig["center_a"]) == 200
    assert await get(key, rig["manager_b"], "manager", rig["center_b"]) == 403
    assert await get(key, rig["admin"], "admin") == 200
    # Ambiguous legacy document: only an admin may open it.
    assert await get(legacy_key, rig["captain_a"], "captain", rig["center_a"]) == 403
    assert await get(legacy_key, rig["admin"], "admin") == 200


def test_photo_capture_accepts_only_our_storage_urls():
    from app.schemas.booking_schema import PhotoCaptureRequest

    assert PhotoCaptureRequest(image_url=own_upload_url("photos/ok.jpg"), latitude=22.7, longitude=75.8)
    for bad in ("https://evil.example/x.jpg", own_upload_url("photos/../../etc/passwd"), own_upload_url("photos/" + "a" * 600)):
        with pytest.raises(pydantic.ValidationError):
            PhotoCaptureRequest(image_url=bad, latitude=22.7, longitude=75.8)


# ---------------------------------------------------------- 7. leave requests


async def test_leave_request_reaches_the_managers_pending_list_and_can_be_reviewed(rig):
    db = rig["db"]
    captain = _auth(rig["captain_a"], "captain", rig["center_a"])
    manager = _auth(rig["manager_a"], "manager", rig["center_a"])
    legacy = await db.leave_requests.insert_one({  # filed before status was written
        "captain_id": rig["captain_a"], "start_date": "2099-01-05", "end_date": "2099-01-06", "reason": "old", "is_deleted": False,
    })
    async with _client() as client:
        filed = await client.post("/api/v1/leave-requests", json={"start_date": "2099-01-01", "end_date": "2099-01-02", "reason": "Family function"}, headers=captain)
        assert filed.status_code == 200 and filed.json()["data"]["status"] == "pending"
        pending = (await client.get(f"/api/v1/leave-requests/center/{rig['center_a']}", headers=manager)).json()["data"]
        assert {filed.json()["data"]["id"], str(legacy.inserted_id)} <= {r["id"] for r in pending}
        approved = await client.put(f"/api/v1/leave-requests/{filed.json()['data']['id']}/review", json={"status": "approved"}, headers=manager)
        legacy_reviewed = await client.put(f"/api/v1/leave-requests/{legacy.inserted_id}/review", json={"status": "rejected"}, headers=manager)
        again = await client.put(f"/api/v1/leave-requests/{filed.json()['data']['id']}/review", json={"status": "rejected"}, headers=manager)
    assert approved.status_code == 200 and approved.json()["data"]["status"] == "approved"
    assert legacy_reviewed.status_code == 200
    assert again.status_code == 400


# ------------------------------------------------ 8. bank details + wallet


async def test_bank_details_are_validated_normalised_and_audited(rig):
    captain = _auth(rig["captain_a"], "captain", rig["center_a"])
    good = {"bank_account_holder": "Test  Captain", "bank_account_number": "1234 5678 9012", "bank_ifsc": " sbin0001234 "}
    async with _client() as client:
        bad_ifsc = await client.put("/api/v1/wallet/bank-details", json={**good, "bank_ifsc": "SBIN1234567"}, headers=captain)
        bad_acct = await client.put("/api/v1/wallet/bank-details", json={**good, "bank_account_number": "12ab34"}, headers=captain)
        short_acct = await client.put("/api/v1/wallet/bank-details", json={**good, "bank_account_number": "12345678"}, headers=captain)
        saved = await client.put("/api/v1/wallet/bank-details", json=good, headers=captain)
    assert bad_ifsc.status_code == bad_acct.status_code == short_acct.status_code == 422
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert (data["bank_ifsc"], data["bank_account_number"], data["bank_account_holder"]) == ("SBIN0001234", "123456789012", "Test Captain")
    log = await rig["db"].audit_logs.find_one({"actor_id": rig["captain_a"], "action": "UPDATE_BANK_DETAILS"})
    assert log is not None and "123456789012" not in str(log)


async def test_parallel_withdrawals_leave_exactly_one_pending_request(rig):
    db = rig["db"]
    await db.captain_wallets.update_one({"captain_id": rig["captain_a"]}, {"$set": {"balance": 100.0}})
    captain = _auth(rig["captain_a"], "captain", rig["center_a"])
    async with _client() as client:
        results = await asyncio.gather(*[client.post("/api/v1/wallet/withdrawals", json={"amount": 100}, headers=captain) for _ in range(6)])
    assert sorted(r.status_code for r in results) == [200, 400, 400, 400, 400, 400]
    assert await db.withdrawal_requests.count_documents({"captain_id": rig["captain_a"], "status": "pending"}) == 1
    refused = next(r for r in results if r.status_code == 400)
    assert "already have a withdrawal" in refused.json()["message"]

    # The balance dropped before review: a clear message, the request stays pending.
    await db.captain_wallets.update_one({"captain_id": rig["captain_a"]}, {"$set": {"balance": 10.0}})
    pending = await db.withdrawal_requests.find_one({"captain_id": rig["captain_a"], "status": "pending"})
    async with _client() as client:
        res = await client.put(f"/api/v1/wallet/withdrawals/{pending['_id']}/review", json={"status": "approved"}, headers=_auth(rig["admin"], "admin"))
    assert res.status_code == 400 and "no longer has" in res.json()["message"]
    assert (await db.withdrawal_requests.find_one({"_id": pending["_id"]}))["status"] == "pending"


async def test_nan_money_is_a_clean_422_and_never_reaches_the_balance(rig):
    db = rig["db"]
    before = (await db.captain_wallets.find_one({"captain_id": rig["captain_b"]}))["balance"]
    async with _client() as client:
        for body in (b'{"amount": NaN, "description": "x"}', b'{"amount": Infinity, "description": "x"}'):
            res = await client.post(
                f"/api/v1/wallet/captain/{rig['captain_b']}/adjust", content=body,
                headers={**_auth(rig["admin"], "admin"), "Content-Type": "application/json"},
            )
            assert res.status_code == 422, res.text
        res = await client.post(
            "/api/v1/wallet/withdrawals", content=b'{"amount": Infinity}',
            headers={**_auth(rig["captain_b"], "captain", rig["center_a"]), "Content-Type": "application/json"},
        )
        assert res.status_code == 422
    assert (await db.captain_wallets.find_one({"captain_id": rig["captain_b"]}))["balance"] == before


async def test_business_settings_reject_values_of_the_wrong_type(rig):
    db = rig["db"]
    original = await db.business_settings.find_one({"_id": "singleton"})
    admin = _auth(rig["admin"], "admin")
    try:
        async with _client() as client:
            for body in (
                {"targets": "oops"}, {"variable_cost_per_wash": "90"}, {"kits_count": True},
                {"targets": {"cac": "high"}}, {"marketing_entries": [{"spend": "lots"}]}, {"marketing_entries": "x"},
            ):
                res = await client.put("/api/v1/analytics/business-settings", json=body, headers=admin)
                assert res.status_code == 400, body
            nan = await client.put(
                "/api/v1/analytics/business-settings", content=b'{"fixed_cost_monthly": NaN}',
                headers={**admin, "Content-Type": "application/json"},
            )
            assert nan.status_code == 400
            ok = await client.put("/api/v1/analytics/business-settings", json={"targets": {"cac": 250}, "kits_count": 2}, headers=admin)
            assert ok.status_code == 200
            read = await client.get("/api/v1/analytics/business-settings", headers=admin)
        assert read.status_code == 200 and read.json()["data"]["targets"]["cac"] == 250
    finally:
        if original is not None:
            await db.business_settings.replace_one({"_id": "singleton"}, original, upsert=True)
        else:
            await db.business_settings.delete_one({"_id": "singleton"})


# --------------------------------------------- AZ-9. service-center access


async def test_center_detail_is_own_center_only_and_public_lookup_is_minimal(rig):
    async with _client() as client:
        own = await client.get(f"/api/v1/service-centers/{rig['center_a']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        other = await client.get(f"/api/v1/service-centers/{rig['center_b']}", headers=_auth(rig["manager_a"], "manager", rig["center_a"]))
        lookup = await client.get("/api/v1/service-centers/lookup", params={"pincode": "452771"})
    assert own.status_code == 200 and other.status_code == 403
    centers = lookup.json()["data"]
    assert [c["id"] for c in centers] == [rig["center_a"]]
    assert centers[0]["location"]["city"] and not {"manager_id", "contact_phone", "contact_email"} & set(centers[0])
    assert "latitude" not in centers[0]["location"]


# ------------------------------------------------------------ 9. pagination


async def test_absurd_page_numbers_are_a_422_not_a_500(db):
    async with _client() as client:
        for page in ("10001", "99999999999999999999"):
            res = await client.get("/api/v1/reviews", params={"page": page})
            assert res.status_code == 422, (page, res.status_code)


# ----------------------------------------------------------- 10. rate limits


def _request(path: str, method: str = "POST", xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({
        "type": "http", "method": method, "path": path, "headers": headers,
        "client": ("169.254.8.1", 40000), "query_string": b"", "server": ("test", 80), "scheme": "http",
    })


@pytest.fixture
def limiter(monkeypatch):
    from app.core import rate_limit
    from app.core.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    monkeypatch.setattr(rate_limit, "_WINDOWS", rate_limit.defaultdict(int))

    async def ok(_request):
        return JSONResponse({"ok": True})

    async def hit(path: str, method: str = "POST", xff: str = "203.0.113.9") -> int:
        return (await rate_limit.rate_limit_middleware(_request(path, method, xff), ok)).status_code

    return hit


def test_ipv6_clients_are_keyed_by_their_64(monkeypatch):
    from app.core import rate_limit
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    key = lambda xff: rate_limit._client_key(_request("/api/v1/x", xff=xff))  # noqa: E731
    assert key("2001:db8:1:2::1") == key("2001:db8:1:2:ffff:ffff:ffff:fffe") == "2001:db8:1:2::/64"
    assert key("2001:db8:1:3::1") != key("2001:db8:1:2::1")
    assert key("203.0.113.7") == "203.0.113.7"


async def test_rotating_ipv6_interface_ids_share_one_login_bucket(limiter):
    codes = [await limiter("/api/v1/auth/login", xff=f"2001:db8:1234:5678::{i + 1:x}") for i in range(16)]
    assert codes[:15] == [200] * 15 and codes[15] == 429


async def test_staff_booking_actions_are_not_throttled_by_the_create_bucket(limiter):
    assert [await limiter("/api/v1/bookings/abc123/heading") for _ in range(30)] == [200] * 30
    creates = [await limiter("/api/v1/bookings") for _ in range(21)]
    assert creates[:20] == [200] * 20 and creates[20] == 429
    assert await limiter("/api/v1/bookings/", xff="203.0.113.10") == 200  # trailing slash still the create bucket
    group = [await limiter("/api/v1/bookings/group", xff="203.0.113.11") for _ in range(21)]
    assert group[20] == 429


async def test_coverage_check_and_offer_lookup_have_tight_buckets(limiter):
    coverage = [await limiter("/api/v1/service-zones/coverage-check") for _ in range(21)]
    assert coverage[:20] == [200] * 20 and coverage[20] == 429
    offers = [await limiter("/api/v1/coupons/public/SAVE10", method="GET") for _ in range(11)]
    assert offers[:10] == [200] * 10 and offers[10] == 429


# ------------------------------------------------------- 12. request body cap


async def _chunks(total: int, size: int = 256 * 1024):
    sent = 0
    while sent < total:
        yield b"x" * min(size, total - sent)
        sent += size


async def test_oversized_bodies_are_refused_with_413(db):
    three_mb = 3 * 1024 * 1024
    async with _client() as client:
        declared = await client.post("/api/v1/auth/login", content=b"x" * three_mb, headers={"Content-Type": "application/json"})
        chunked = await client.post("/api/v1/contact", content=_chunks(three_mb), headers={"Content-Type": "application/json"})
        # Upload routes allow the file cap + framing — still refused above it,
        # before any auth or multipart parsing.
        upload = await client.post("/api/v1/uploads/photo", content=b"x" * (10 * 1024 * 1024), headers={"Content-Type": "multipart/form-data; boundary=x"})
        normal = await client.post("/api/v1/auth/login", json={"identifier": "9000000000", "password": "x"})
    for res in (declared, chunked, upload):
        assert res.status_code == 413, res.text
        assert res.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert normal.status_code != 413
