"""A phone with no account can log in by code: the code is checked, the NAME
is asked next, and the account is created with it (founder 2026-10-09)."""
import itertools

import pytest

from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio
_PHONE = itertools.count(9744400001)


def _p() -> str:
    return str(next(_PHONE))


async def _code(db, phone: str) -> str:
    return (await db["otp_requests"].find_one({"identifier": phone}))["otp"]


async def _cleanup(db, phone: str):
    await db.users.delete_many({"phone": phone})
    await db["otp_requests"].delete_many({"identifier": phone})
    await db["otp_requests"].delete_many({"_id": f"otp-send-cap:{phone}"})


async def test_new_number_gets_a_code_then_the_name_step_then_an_account(db):
    phone = _p()
    try:
        async with h.client() as c:
            r = await c.post("/api/v1/auth/otp/request", json={"identifier": phone})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["new_account"] is True
            code = await _code(db, phone)
            # 1) the right code, no name: only checked — nothing created, code kept
            r1 = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": code})
            assert r1.status_code == 200 and r1.json()["data"] == {"needs_name": True}
            assert await db.users.find_one({"phone": phone}) is None
            assert await _code(db, phone) == code, "the code must survive the name step"
            # 2) same code + name: account created and signed in
            r2 = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": code, "full_name": "  Asha   Verma "})
            assert r2.status_code == 200, r2.text
            data = r2.json()["data"]
            assert data["access_token"] and data["user"]["role"] == "customer"
            user = await db.users.find_one({"phone": phone})
            assert user["full_name"] == "Asha Verma" and user["phone_verified"] is True
            # the code is spent: it can't create or log in again
            r3 = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": code, "full_name": "Again"})
            assert r3.status_code == 400
    finally:
        await _cleanup(db, phone)


async def test_wrong_code_creates_nothing_and_asks_no_name(db):
    phone = _p()
    try:
        async with h.client() as c:
            await c.post("/api/v1/auth/otp/request", json={"identifier": phone})
            code = await _code(db, phone)
            wrong = "000000" if code != "000000" else "111111"
            r = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": wrong, "full_name": "Mallory"})
            assert r.status_code == 400
            r = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": wrong})
            assert r.status_code == 400
        assert await db.users.find_one({"phone": phone}) is None
    finally:
        await _cleanup(db, phone)


async def test_name_alone_never_creates_an_account(db):
    phone = _p()
    try:
        async with h.client() as c:
            r = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": "123456", "full_name": "No Proof"})
            assert r.status_code == 400
        assert await db.users.find_one({"phone": phone}) is None
    finally:
        await _cleanup(db, phone)


async def test_existing_customer_login_is_unchanged(db):
    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    user = await db.users.find_one({"_id": h.oid(cu["id"])})
    phone = user["phone"]
    async with h.client() as c:
        r = await c.post("/api/v1/auth/otp/request", json={"identifier": phone})
        assert r.status_code == 200 and r.json()["data"]["new_account"] is False
        code = await _code(db, phone)
        r2 = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": code})
        assert r2.status_code == 200 and r2.json()["data"]["access_token"]
    await db["otp_requests"].delete_many({"identifier": phone})


async def test_staff_phone_still_refused(db):
    cid, _pin = await h.center(db)
    mgr = await h.manager(db, cid)
    user = await db.users.find_one({"_id": h.oid(mgr["id"])})
    if not user.get("phone"):
        pytest.skip("manager factory has no phone")
    async with h.client() as c:
        r = await c.post("/api/v1/auth/otp/request", json={"identifier": user["phone"]})
    assert r.status_code == 400 and "staff" in r.text.lower()


async def test_code_login_clears_the_set_a_password_gate(db):
    """A guest sign-up / manager-created customer carries must_change_password;
    after proving the phone by code they sign in without a password screen,
    and the old temporary password stops working."""
    from app.core.security import hash_password

    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    uid = h.oid(cu["id"])
    user = await db.users.find_one({"_id": uid})
    phone = user["phone"]
    await db.users.update_one({"_id": uid}, {"$set": {"must_change_password": True, "password_hash": hash_password("Temp@12345"), "token_version": 3}})
    async with h.client() as c:
        await c.post("/api/v1/auth/otp/request", json={"identifier": phone})
        code = await _code(db, phone)
        r = await c.post("/api/v1/auth/otp-login", json={"phone": phone, "otp": code})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["user"]["must_change_password"] is False
        old = await c.post("/api/v1/auth/login", json={"identifier": phone, "password": "Temp@12345"})
        assert old.status_code == 401
    after = await db.users.find_one({"_id": uid})
    assert after["must_change_password"] is False and after["password_hash"] is None and after["token_version"] > 3
    await db["otp_requests"].delete_many({"identifier": phone})


async def test_boot_task_clears_customer_gates_but_never_staff(db):
    from app.core.security import hash_password
    from app.services.auth_service import clear_customer_password_gates
    from tests.factories import make_manager

    cid, pin = await h.center(db)
    cu = await h.customer(db, pin)
    mid = await make_manager(db, cid)
    await db.users.update_one({"_id": h.oid(cu["id"])}, {"$set": {"must_change_password": True, "password_hash": hash_password("Temp@12345")}})
    await db.users.update_one({"_id": h.oid(mid)}, {"$set": {"must_change_password": True}})
    assert await clear_customer_password_gates(db) >= 1
    c_after = await db.users.find_one({"_id": h.oid(cu["id"])})
    m_after = await db.users.find_one({"_id": h.oid(mid)})
    assert c_after["must_change_password"] is False and c_after["password_hash"] is None
    assert m_after["must_change_password"] is True and m_after["password_hash"], "staff keep their temp-password gate"
    assert await clear_customer_password_gates(db) == 0
