"""
Manager phone + WhatsApp policy.

Production incident (2026-09-28): the only manager's phone was the
business's own WhatsApp number, so Meta refused every alert with "(#100)
Invalid parameter" (562 failed sends), and neither the manager nor an admin
could change the number from the portal. Also the founder's rule: a
manager gets WhatsApp for NEW BOOKINGS only — everything else stays in-app.
"""
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException
from app.schemas.user_schema import AdminUserUpdateRequest, StaffCreateRequest
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.user_service import UserService
from app.services.whatsapp_service import MetaCloudWhatsAppProvider
from app.utils.phone import is_business_whatsapp_number

from tests.factories import make_customer, make_manager, make_service_center

BUSINESS = "918962200001"
BUSINESS_LOCAL = "8962200001"


@pytest.fixture
def business_number(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_BUSINESS_NUMBER", BUSINESS)
    return BUSINESS_LOCAL


@pytest.fixture
async def manager(db, cleanup):
    center_id = await make_service_center(db)
    manager_id = await make_manager(db, center_id)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("notifications", {"user_id": manager_id}))
    doc = await db.users.find_one({"_id": ObjectId(manager_id)})
    cleanup.append(("whatsapp_outbox", {"phone": doc["phone"]}))
    return {"id": manager_id, "center_id": center_id, "phone": doc["phone"]}


def _auth(user_id: str, role: str, center_id: str | None = None) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, role, {'service_center_id': center_id, 'tv': 0})}"}


# ------------------------------------------------------------ WhatsApp policy


@pytest.mark.asyncio
async def test_manager_gets_whatsapp_only_for_new_bookings(db, manager):
    notes = NotificationService(db)
    for title in ("Still needs a captain", "Captain hasn't started", "Delay reported", "New complaint"):
        await notes.notify(manager["id"], title, "details")
    assert await db.whatsapp_outbox.count_documents({"phone": manager["phone"]}) == 0
    # Every one of them is still in the portal's bell.
    assert await db.notifications.count_documents({"user_id": manager["id"]}) == 4

    await notes.notify(
        manager["id"], "New booking — Star Wash", "details",
        wa_event="manager_new_booking", wa_params=["A", "9000000000", "Car", "Star Wash", "Today", "Vijay Nagar"],
    )
    assert await db.whatsapp_outbox.count_documents({"phone": manager["phone"]}) == 1


@pytest.mark.asyncio
async def test_customers_still_get_whatsapp_updates(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    cleanup.append(("notifications", {"user_id": customer_id}))
    phone = (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]
    cleanup.append(("whatsapp_outbox", {"phone": phone}))

    await NotificationService(db).notify(customer_id, "Captain assigned", "details")
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1


# ----------------------------------------------------- business number guard


def test_business_number_matches_any_spelling(business_number):
    assert is_business_whatsapp_number("8962200001")
    assert is_business_whatsapp_number("+91 89622 00001")
    assert is_business_whatsapp_number("918962200001")
    assert not is_business_whatsapp_number("8962200002")


def test_no_business_number_configured_means_no_check(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_BUSINESS_NUMBER", "")
    assert not is_business_whatsapp_number("8962200001")


@pytest.mark.asyncio
async def test_meta_send_to_own_number_is_skipped_without_calling_meta(db, business_number, monkeypatch, cleanup):
    import app.services.whatsapp_service as wa_mod

    def _boom():
        raise AssertionError("Meta must not be called for our own number")

    monkeypatch.setattr(wa_mod, "_meta_http", _boom)
    cleanup.append(("whatsapp_outbox", {"phone": business_number}))
    provider = MetaCloudWhatsAppProvider(db, "token", "phone-id", "v21.0")

    assert await provider.send_template(business_number, "blussit_service_update", "en_US", ["t", "m"]) is False
    row = await db.whatsapp_outbox.find_one({"phone": business_number})
    assert row["ok"] is False and "own WhatsApp number" in row["error"]


@pytest.mark.asyncio
async def test_business_number_refused_everywhere_a_phone_is_set(db, manager, business_number, cleanup):
    auth = AuthService(db)
    with pytest.raises(BadRequestException):
        await auth.add_phone_request(manager["id"], business_number)
    with pytest.raises(BadRequestException):
        await auth.add_phone_confirm(manager["id"], business_number, otp="123456")
    with pytest.raises(BadRequestException):
        await UserService(db).admin_update_user(manager["id"], AdminUserUpdateRequest(phone=business_number))
    with pytest.raises(BadRequestException):
        await auth.create_staff_account(
            StaffCreateRequest(full_name="Dup Manager", phone=business_number, password="Test@12345", role="manager", service_center_id=manager["center_id"]),
            created_by="test",
        )
    assert (await db.users.find_one({"_id": ObjectId(manager["id"])}))["phone"] == manager["phone"]


# -------------------------------------------------------- changing the number


@pytest.mark.asyncio
async def test_manager_changes_own_number_with_otp(db, manager, cleanup):
    new_phone = "9777766101"
    cleanup.append(("otp_requests", {"identifier": new_phone}))
    cleanup.append(("whatsapp_outbox", {"phone": new_phone}))
    auth = AuthService(db)

    await auth.add_phone_request(manager["id"], new_phone)
    code = (await db.otp_requests.find_one({"identifier": new_phone}))["otp"]
    with pytest.raises(BadRequestException):
        await auth.add_phone_confirm(manager["id"], new_phone, otp="000000")
    await auth.add_phone_confirm(manager["id"], new_phone, otp=code)

    fresh = await db.users.find_one({"_id": ObjectId(manager["id"])})
    assert fresh["phone"] == new_phone and fresh["phone_verified"] is True

    # New-booking alerts now go to the new number (the OTP was one row already).
    before = await db.whatsapp_outbox.count_documents({"phone": new_phone})
    await NotificationService(db).notify(
        manager["id"], "New booking", "details", wa_event="manager_new_booking", wa_params=["A", "B", "C", "D", "E", "F"],
    )
    assert await db.whatsapp_outbox.count_documents({"phone": new_phone}) == before + 1


@pytest.mark.asyncio
async def test_admin_edits_staff_phone(db, manager, cleanup):
    svc = UserService(db)
    updated = await svc.admin_update_user(manager["id"], AdminUserUpdateRequest(phone="+91 97777 66102"))
    assert updated["phone"] == "9777766102"
    doc = await db.users.find_one({"_id": ObjectId(manager["id"])})
    assert doc["phone_verified"] is False  # set by an admin, not proven by OTP

    # Blank = leave the number alone (other fields still save).
    await svc.admin_update_user(manager["id"], AdminUserUpdateRequest(phone="", full_name="Renamed Manager"))
    doc = await db.users.find_one({"_id": ObjectId(manager["id"])})
    assert doc["phone"] == "9777766102" and doc["full_name"] == "Renamed Manager"

    # Someone else's number is refused.
    other_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(other_id)}))
    other_phone = (await db.users.find_one({"_id": ObjectId(other_id)}))["phone"]
    with pytest.raises(ConflictException):
        await svc.admin_update_user(manager["id"], AdminUserUpdateRequest(phone=other_phone))

    with pytest.raises(ValueError):
        AdminUserUpdateRequest(phone="12345")


@pytest.mark.asyncio
async def test_admin_phone_edit_over_http(db, manager):
    from app.main import app

    admin = await db.users.find_one({"role": "admin"})
    headers = _auth(str(admin["_id"]), "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.put(f"/api/v1/users/{manager['id']}", json={"phone": "9777766103"}, headers=headers)
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["phone"] == "9777766103"
        bad = await client.put(f"/api/v1/users/{manager['id']}", json={"phone": "555"}, headers=headers)
        assert bad.status_code == 422
        # A manager can't edit accounts through the admin route.
        denied = await client.put(
            f"/api/v1/users/{manager['id']}", json={"phone": "9777766104"}, headers=_auth(manager["id"], "manager", manager["center_id"]),
        )
        assert denied.status_code == 403
