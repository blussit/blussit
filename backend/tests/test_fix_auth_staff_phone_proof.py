"""
AUTH-05 regression tests — a staff member's phone is never treated as a
customer's phone proof. The WhatsApp bot (and every quick-booking path)
goes through AuthService.mark_phone_proven / ensure_customer_by_phone; for
a manager or captain those must not adopt the account, verify the phone,
wipe the password or revoke sessions — they raise StaffAccountPhoneError,
which callers can tell apart.
"""
from datetime import datetime, timezone

import httpx
import pytest
from bson import ObjectId

from app.core.security import hash_password
from app.services.auth_service import AuthService, StaffAccountPhoneError

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient


def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def _wa(phone10: str, text: str, n: int) -> dict:
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": f"91{phone10}", "profile": {"name": "Field Staff"}}],
        "messages": [{"from": f"91{phone10}", "id": f"wamid.FIXAUTH{phone10}{n}", "type": "text", "text": {"body": text}}],
    }}]}]}


async def _staff(db, cleanup, role: str, phone: str) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "full_name": f"Fix {role}", "email": f"fix.proof.{role}.{phone}@example.com", "phone": phone,
        "password_hash": hash_password("Staff@12345"), "role": role, "status": "active",
        "is_deleted": False, "created_at": now, "updated_at": now, "token_version": 3,
    }
    uid = str((await db.users.insert_one(doc)).inserted_id)
    cleanup.append(("users", {"_id": ObjectId(uid)}))
    cleanup.append(("whatsapp_conversations", {"wa_id": f"91{phone}"}))
    cleanup.append(("whatsapp_outbox", {"phone": phone}))
    cleanup.append(("whatsapp_message_dedup", {"wamid": {"$regex": f"^wamid.FIXAUTH{phone}"}}))
    cleanup.append(("whatsapp_inbox", {"wa_id": f"91{phone}"}))
    return {"id": uid, **doc}


@pytest.mark.parametrize("role,phone", [("manager", "9744400001"), ("captain", "9744400002"), ("admin", "9744400003")])
async def test_bot_message_from_staff_phone_leaves_the_account_alone(db, cleanup, role, phone):
    from app.services.whatsapp_bot_service import WhatsAppBotService

    staff = await _staff(db, cleanup, role, phone)
    async with _client() as c:
        before = await c.post("/api/v1/auth/login", json={"identifier": staff["email"], "password": "Staff@12345"})
        assert before.status_code == 200, before.text
        await WhatsAppBotService(db).handle_webhook(_wa(phone, "hi", 1))
        await WhatsAppBotService(db).handle_webhook(_wa(phone, "Ramesh", 2))
        after = await c.post("/api/v1/auth/login", json={"identifier": staff["email"], "password": "Staff@12345"})
    assert after.status_code == 200, after.text
    stored = await db.users.find_one({"_id": ObjectId(staff["id"])})
    assert stored["password_hash"] and stored["token_version"] == 3
    assert not stored.get("phone_verified") and "phone_verified_at" not in stored
    convo = await db.whatsapp_conversations.find_one({"wa_id": f"91{phone}"})
    assert not convo or convo.get("customer_id") != staff["id"]
    # No customer account was created for the number either.
    assert await db.users.count_documents({"phone": phone}) == 1


@pytest.mark.parametrize("role,phone", [("manager", "9744400011"), ("captain", "9744400012")])
async def test_phone_proof_paths_raise_a_distinguishable_error_for_staff(db, cleanup, role, phone):
    staff = await _staff(db, cleanup, role, phone)
    auth = AuthService(db)
    user = await db.users.find_one({"_id": ObjectId(staff["id"])})
    with pytest.raises(StaffAccountPhoneError) as exc:
        await auth.mark_phone_proven(user, last_login_at=datetime.now(timezone.utc))
    assert exc.value.status_code == 400 and exc.value.error_code == "STAFF_ACCOUNT_PHONE"
    with pytest.raises(StaffAccountPhoneError):
        await auth.ensure_customer_by_phone(phone, "Someone")
    stored = await db.users.find_one({"_id": ObjectId(staff["id"])})
    assert stored["password_hash"] == user["password_hash"] and stored["token_version"] == 3
    assert not stored.get("phone_verified") and "last_login_at" not in stored
