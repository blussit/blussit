"""
Manager/admin-triggered "forgot password" reset for a customer — the
generated temp password must never be returned to the caller (it goes
straight to the customer's WhatsApp instead), must actually let the
customer log in, and must force a real password change on next login.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.core.security import verify_password
from app.services.auth_service import AuthService

from tests.factories import make_captain, make_customer, make_manager, make_service_center


@pytest.fixture
async def rig(db, cleanup):
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    manager_id = await make_manager(db, center_id)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    return {"db": db, "manager_id": manager_id, "customer_id": customer_id, "center_id": center_id}


@pytest.mark.asyncio
async def test_reset_generates_a_working_password_and_never_returns_it(rig, cleanup):
    auth = AuthService(rig["db"])
    original = await rig["db"].users.find_one({"_id": ObjectId(rig["customer_id"])})
    cleanup.append(("whatsapp_outbox", {"phone": original["phone"]}))

    result = await auth.staff_reset_customer_password(rig["customer_id"], rig["manager_id"])
    assert result is None  # nothing returned to the caller — not even wrapped in a dict

    updated = await rig["db"].users.find_one({"_id": ObjectId(rig["customer_id"])})
    assert updated["password_hash"] != original["password_hash"]
    assert updated["must_change_password"] is True

    # The only place the plaintext temp password exists is the WhatsApp
    # outbox record (simulating what actually reached the customer) —
    # recover it from there ONLY to prove login now works with it.
    outbox_entry = await rig["db"].whatsapp_outbox.find_one({"phone": original["phone"]}, sort=[("created_at", -1)])
    assert outbox_entry is not None
    assert "Temporary password:" in outbox_entry["message"]
    temp_password = outbox_entry["message"].split("Temporary password:")[1].split("\n")[0].strip()
    assert verify_password(temp_password, updated["password_hash"])

    login_result = await auth.login(original["phone"], temp_password)
    assert login_result["user"]["id"] == rig["customer_id"]
    assert login_result["user"]["must_change_password"] is True


@pytest.mark.asyncio
async def test_reset_rejects_a_nonexistent_customer(rig):
    auth = AuthService(rig["db"])
    with pytest.raises(NotFoundException):
        await auth.staff_reset_customer_password(str(ObjectId()), rig["manager_id"])


@pytest.mark.asyncio
async def test_reset_rejects_a_non_customer_account(rig, db, cleanup):
    captain_id = await make_captain(db, rig["center_id"])
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    auth = AuthService(db)
    with pytest.raises(ForbiddenException):
        await auth.staff_reset_customer_password(captain_id, rig["manager_id"])


@pytest.mark.asyncio
async def test_reset_rejects_a_customer_with_no_phone_on_file(rig, db, cleanup):
    # A customer created with only an email, no phone.
    result = await db.users.insert_one({
        "full_name": "No Phone Customer", "email": "nophone.customer@example.com", "password_hash": "x",
        "role": "customer", "status": "active", "is_deleted": False,
    })
    customer_id = str(result.inserted_id)
    cleanup.append(("users", {"_id": result.inserted_id}))

    auth = AuthService(db)
    with pytest.raises(BadRequestException, match="no phone number"):
        await auth.staff_reset_customer_password(customer_id, rig["manager_id"])
