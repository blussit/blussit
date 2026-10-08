"""Founder rule 2026-10-08: account emails are stored lower-case and login
by email is case-insensitive ("Manager.Indore@…" logs in as
"manager.indore@…"); existing mixed-case emails are lower-cased at boot."""
import uuid

import pytest
from bson import ObjectId

from app.repositories.user_repository import lowercase_user_emails
from tests import test_fix_core_helpers as h
from tests.factories import make_manager

pytestmark = pytest.mark.asyncio


def _email(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@doorstepvehiclecare.in"


async def test_login_by_email_ignores_case(db):
    cid, _pin = await h.center(db)
    mid = await make_manager(db, cid)
    email = _email("mgr")
    await db.users.update_one({"_id": ObjectId(mid)}, {"$set": {"email": email}})
    async with h.client() as c:
        mixed = await c.post("/api/v1/auth/login", json={"identifier": "  " + email.capitalize() + " ", "password": "Test@12345"})
        upper = await c.post("/api/v1/auth/login", json={"identifier": email.upper(), "password": "Test@12345"})
    assert mixed.status_code == 200, mixed.text
    assert upper.status_code == 200, upper.text


async def test_staff_created_with_mixed_case_email_is_stored_lower_case(db):
    cid, _pin = await h.center(db)
    admin = await h.admin(db)
    email = _email("Captain.Mixed").replace("captain", "Captain")
    async with h.client() as c:
        r = await c.post("/api/v1/auth/staff", headers=admin["h"], json={
            "full_name": "Mixed Case Captain", "email": email, "phone": f"98{uuid.uuid4().int % 10**8:08d}",
            "password": "Captain@12345", "role": "manager", "service_center_id": cid,
        })
    assert r.status_code in (200, 201), r.text
    stored = await db.users.find_one({"email": email.lower()})
    assert stored is not None and stored["email"] == email.lower()
    # The same address in another case is a duplicate.
    async with h.client() as c:
        dup = await c.post("/api/v1/auth/staff", headers=admin["h"], json={
            "full_name": "Dup Captain", "email": email.upper(), "phone": f"97{uuid.uuid4().int % 10**8:08d}",
            "password": "Captain@12345", "role": "manager", "service_center_id": cid,
        })
    assert dup.status_code == 409, dup.text


async def test_boot_task_lower_cases_existing_emails_and_skips_collisions(db):
    cid, _pin = await h.center(db)
    a, b, c_ = await make_manager(db, cid), await make_manager(db, cid), await make_manager(db, cid)
    plain, clash = _email("Old.Staff"), _email("clash")
    await db.users.update_one({"_id": ObjectId(a)}, {"$set": {"email": plain}})              # mixed case, free
    await db.users.update_one({"_id": ObjectId(b)}, {"$set": {"email": clash}})              # lower already
    await db.users.update_one({"_id": ObjectId(c_)}, {"$set": {"email": clash.upper()}})     # would collide
    first = await lowercase_user_emails(db)
    assert first >= 1
    assert (await db.users.find_one({"_id": ObjectId(a)}))["email"] == plain.lower()
    assert (await db.users.find_one({"_id": ObjectId(c_)}))["email"] == clash.upper(), "a collision is left for an admin"
    await lowercase_user_emails(db)  # idempotent: re-running changes nothing for these rows
    assert (await db.users.find_one({"_id": ObjectId(a)}))["email"] == plain.lower()


def test_user_repository_keeps_all_its_methods():
    """A helper added to the module must never swallow the class's methods
    (list_by_role once fell out of UserRepository and 500'd two pages)."""
    from app.repositories.user_repository import UserRepository

    for name in ("find_by_email", "find_by_phone", "find_by_identifier", "list_by_role"):
        assert callable(getattr(UserRepository, name, None)), name
