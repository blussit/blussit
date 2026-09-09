"""
Brute-force protection on password login: 5 consecutive wrong passwords
lock the account for a few minutes; a correct password resets the
counter; the lock message never confirms whether the password was right.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import UnauthorizedException
from app.services.auth_service import AuthService

from tests.factories import make_customer


@pytest.mark.asyncio
async def test_five_failures_lock_the_account(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    auth = AuthService(db)

    for _ in range(AuthService.LOGIN_MAX_FAILURES):
        with pytest.raises(UnauthorizedException, match="Invalid credentials"):
            await auth.login(user["email"], "Wrong@99999")

    # 6th attempt is blocked even WITH the correct password — locked out.
    with pytest.raises(UnauthorizedException, match="Too many failed attempts"):
        await auth.login(user["email"], "Test@12345")


@pytest.mark.asyncio
async def test_success_resets_the_failure_counter(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    auth = AuthService(db)

    for _ in range(AuthService.LOGIN_MAX_FAILURES - 1):
        with pytest.raises(UnauthorizedException):
            await auth.login(user["email"], "Wrong@99999")
    assert (await auth.login(user["email"], "Test@12345"))["user"] is not None

    fresh = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert fresh.get("failed_login_attempts", 0) == 0
    # One more failure after the reset must NOT lock (counter restarted).
    with pytest.raises(UnauthorizedException, match="Invalid credentials"):
        await auth.login(user["email"], "Wrong@99999")


@pytest.mark.asyncio
async def test_expired_lock_allows_login_again(db, cleanup):
    customer_id = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer_id)}))
    await db.users.update_one(
        {"_id": ObjectId(customer_id)},
        {"$set": {"login_locked_until": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    user = await db.users.find_one({"_id": ObjectId(customer_id)})
    assert (await AuthService(db).login(user["email"], "Test@12345"))["user"] is not None
