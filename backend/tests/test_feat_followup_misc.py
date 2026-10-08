"""Follow-up 2026-10-07 — small leftovers: the admin's captain-withdrawal
queue names the captain (one batched lookup), and a staff password reset by
code that is refused carries a stable error_code."""
import pytest

from app.core.exceptions import StaffResetRefusedException
from app.services.auth_service import OTP_PASSWORD_RESET, AuthService
from app.services.wallet_service import WalletService
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def test_withdrawal_queue_carries_the_captain_name(db, monkeypatch):
    cid, _pin = await h.center(db)
    caps = [await h.captain(db, cid, wallet=900.0) for _ in range(2)]
    svc = WalletService(db)
    for cap in caps:
        await svc.request_withdrawal(cap["id"], 100.0)
    names = {
        str(u["_id"]): u["full_name"]
        for u in await db.users.find({"_id": {"$in": [h.oid(c["id"]) for c in caps]}}).to_list(None)
    }

    from motor.motor_asyncio import AsyncIOMotorCollection

    calls = {"users.find_one": 0}
    real_find_one = AsyncIOMotorCollection.find_one

    def counting_find_one(self, *a, **kw):
        if self.name == "users":
            calls["users.find_one"] += 1
        return real_find_one(self, *a, **kw)

    monkeypatch.setattr(AsyncIOMotorCollection, "find_one", counting_find_one)
    rows, _total = await svc.list_pending_withdrawals(1, 200)
    monkeypatch.undo()
    mine = {r["captain_id"]: r for r in rows if r["captain_id"] in names}
    assert set(mine) == set(names)
    for captain_id, row in mine.items():
        assert row["captain_name"] == names[captain_id]
    assert calls["users.find_one"] == 0  # batched, never one lookup per row


async def test_staff_code_reset_refusal_has_a_stable_error_code(db):
    admin = await db.users.find_one({"email": "admin@doorstepvehiclecare.in"})
    with pytest.raises(StaffResetRefusedException) as exc:
        await AuthService(db).request_otp(admin["email"], OTP_PASSWORD_RESET)
    assert exc.value.error_code == "STAFF_RESET_REFUSED" and exc.value.status_code == 400
    async with h.client() as c:
        for path, body in (
            ("/api/v1/auth/forgot-password", {"identifier": admin["email"]}),
            ("/api/v1/auth/reset-password", {"identifier": admin["phone"], "otp": "123456", "new_password": "Owned@123456"}),
        ):
            r = await c.post(path, json=body)
            assert r.status_code == 400, r.text
            assert r.json()["error_code"] == "STAFF_RESET_REFUSED" and "admin" in r.json()["message"]
