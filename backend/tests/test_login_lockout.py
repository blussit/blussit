"""
Password-login throttling (founder rule 2026-10-28): wrong passwords are
throttled per (account + device + source IP), escalating — never account-wide,
so a stranger can't lock the owner out. A device that signed in before is never
blocked; an account under attack elsewhere only SLOWS unknown devices; one IP
hammering many accounts is blocked as credential stuffing.
"""
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from bson import ObjectId

from app.core import rate_limit
from app.core.config import settings
from app.core.exceptions import TooManyRequestsException, UnauthorizedException
from app.services import auth_guard as guard
from app.services.auth_service import AuthService
from tests.factories import TEST_PASSWORD, make_customer

pytestmark = pytest.mark.asyncio
_RealAsyncClient = httpx.AsyncClient
DEV_A = "a" * 24
DEV_B = "b" * 24
DEV_T = "t" * 24


def _as(ip: str | None, device: str | None):
    """Pretend the next calls arrive from this IP bucket / device."""
    rate_limit._request_client.set(ip)
    rate_limit._request_device.set(device)


@pytest.fixture(autouse=True)
def _forget_client():
    yield
    _as(None, None)


async def _customer(db, cleanup) -> dict:
    cid = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(cid)}))
    cleanup.append(("rate_counters", {"_id": {"$regex": "^login"}}))
    return await db.users.find_one({"_id": ObjectId(cid)})


async def _fail(auth, user, times: int):
    for _ in range(times):
        try:
            await auth.login(user["email"], "Wrong@99999")
        except (UnauthorizedException, TooManyRequestsException):
            pass


async def test_a_strangers_failures_lock_only_their_own_device_and_ip(db, cleanup):
    user = await _customer(db, cleanup)
    auth = AuthService(db)

    _as("203.0.113.9", DEV_A)
    for _ in range(5):
        with pytest.raises(UnauthorizedException, match="Invalid credentials"):
            await auth.login(user["email"], "Wrong@99999")
    # Their 6th try — even with the right password — waits (about a minute).
    with pytest.raises(TooManyRequestsException) as hit:
        await auth.login(user["email"], TEST_PASSWORD)
    assert 55 <= hit.value.details["retry_after"] <= 60 and hit.value.status_code == 429

    # The owner, on another device and another address, signs in at once.
    _as("198.51.100.4", DEV_B)
    assert (await auth.login(user["email"], TEST_PASSWORD))["user"]["id"] == str(user["_id"])
    # The account itself is not marked locked anywhere.
    fresh = await db.users.find_one({"_id": user["_id"]})
    assert not fresh.get("login_locked_until")


async def test_the_lock_escalates_one_five_thirty_minutes(db, cleanup):
    user = await _customer(db, cleanup)
    account = str(user["_id"])
    bucket = guard.login_bucket_id(account, DEV_A, "203.0.113.9")
    seen = {}
    for n in range(1, 26):
        await guard.login_failed(db, account, DEV_A, "203.0.113.9")
        doc = await db.rate_counters.find_one({"_id": bucket})
        locked = doc.get("locked_until")
        seen[n] = round((locked.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()) if locked else 0
    assert seen[4] == 0
    assert 58 <= seen[5] <= 60 and 58 <= seen[9] <= 60
    assert 298 <= seen[10] <= 300 and 298 <= seen[19] <= 300
    assert 1798 <= seen[20] <= 1800 and 1798 <= seen[25] <= 1800


async def test_a_success_clears_the_bucket(db, cleanup):
    user = await _customer(db, cleanup)
    auth = AuthService(db)
    _as("203.0.113.9", DEV_A)
    await _fail(auth, user, 4)
    assert (await auth.login(user["email"], TEST_PASSWORD))["user"] is not None
    assert await db.rate_counters.find_one({"_id": guard.login_bucket_id(str(user["_id"]), DEV_A, "203.0.113.9")}) is None
    await _fail(auth, user, 4)  # restarted: still below the first step
    assert (await auth.login(user["email"], TEST_PASSWORD))["user"] is not None


async def test_a_device_that_signed_in_before_is_never_blocked(db, cleanup):
    user = await _customer(db, cleanup)
    auth = AuthService(db)
    _as("203.0.113.50", DEV_T)
    await auth.login(user["email"], TEST_PASSWORD)
    stored = (await db.users.find_one({"_id": user["_id"]}))["trusted_devices"]
    assert stored == [guard.device_hash(DEV_T)] and DEV_T not in stored  # hashed, not the raw id

    # 30 wrong passwords later (a typo storm, or someone abusing its address)...
    await _fail(auth, user, 30)
    # ...the trusted device is still let in, and an unknown device on that address is not.
    assert (await auth.login(user["email"], TEST_PASSWORD))["user"] is not None
    _as("203.0.113.50", DEV_A)
    await _fail(auth, user, 5)
    with pytest.raises(TooManyRequestsException):
        await auth.login(user["email"], TEST_PASSWORD)


async def test_trusted_device_list_is_bounded(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_DEVICES_MAX", 3)
    user = await _customer(db, cleanup)
    auth = AuthService(db)
    for i in range(5):
        _as("203.0.113.60", f"{i}" * 20)
        await auth.login(user["email"], TEST_PASSWORD)
    stored = (await db.users.find_one({"_id": user["_id"]}))["trusted_devices"]
    assert stored == [guard.device_hash(f"{i}" * 20) for i in (2, 3, 4)]


async def test_one_ip_hammering_many_accounts_is_blocked_but_not_trusted_devices(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_IP_FAIL_CEILING", 6)
    victim = await _customer(db, cleanup)
    other = await _customer(db, cleanup)
    auth = AuthService(db)
    _as("203.0.113.70", DEV_T)
    await auth.login(victim["email"], TEST_PASSWORD)  # DEV_T is now trusted for the victim

    _as("203.0.113.70", DEV_A)
    for _ in range(7):  # many different accounts, each only a few times
        try:
            await auth.login(f"nobody{time.time_ns()}@example.com", "Wrong@99999")
        except UnauthorizedException:
            pass
    # A brand-new bucket on that address is now refused (credential stuffing)...
    _as("203.0.113.70", DEV_B)
    with pytest.raises(TooManyRequestsException, match="from this network"):
        await auth.login(other["email"], TEST_PASSWORD)
    # ...a device that signed in to that account before is not.
    _as("203.0.113.70", DEV_T)
    assert (await auth.login(victim["email"], TEST_PASSWORD))["user"] is not None
    # And another address is unaffected.
    _as("198.51.100.99", DEV_B)
    assert (await auth.login(other["email"], TEST_PASSWORD))["user"] is not None


async def test_account_wide_failures_only_slow_unknown_devices(db, cleanup, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ACCOUNT_SLOWDOWN_FAILS", 3)
    monkeypatch.setattr(settings, "LOGIN_ACCOUNT_SLOWDOWN_SECONDS", 0.4)
    user = await _customer(db, cleanup)
    auth = AuthService(db)
    _as("203.0.113.80", DEV_T)
    await auth.login(user["email"], TEST_PASSWORD)  # trusted
    for i in range(4):  # an attack from four different places
        _as(f"192.0.2.{i + 1}", f"{i}" * 20)
        await _fail(auth, user, 1)

    # Compare the two logins rather than a fixed time: bcrypt alone can take
    # ~1 s on a busy machine, so an absolute ceiling measures the hardware.
    _as("203.0.113.80", DEV_T)  # the trusted device is not slowed at all
    started = time.monotonic()
    await auth.login(user["email"], TEST_PASSWORD)
    trusted_took = time.monotonic() - started

    _as("198.51.100.5", DEV_B)  # an unknown device: slowed, but let in with the right password
    started = time.monotonic()
    assert (await auth.login(user["email"], TEST_PASSWORD))["user"] is not None
    unknown_took = time.monotonic() - started
    assert unknown_took - trusted_took >= 0.25


async def test_an_unknown_identifier_is_throttled_exactly_like_a_real_one(db, cleanup):
    cleanup.append(("rate_counters", {"_id": {"$regex": "^login"}}))
    auth = AuthService(db)
    _as("203.0.113.90", DEV_A)
    for _ in range(5):
        with pytest.raises(UnauthorizedException, match="Invalid credentials"):
            await auth.login("ghost@example.com", "Wrong@99999")
    with pytest.raises(TooManyRequestsException):
        await auth.login("GHOST@example.com", "Wrong@99999")


async def test_the_http_layer_reads_the_device_header_and_answers_429_with_retry_after(db, cleanup, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    user = await _customer(db, cleanup)

    def post(c, password, device, ip="203.0.113.123"):
        headers = {"X-Forwarded-For": ip}
        if device:
            headers["X-Device-Id"] = device
        return c.post("/api/v1/auth/login", headers=headers, json={"identifier": user["email"], "password": password})

    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30) as c:
        for _ in range(5):
            assert (await post(c, "Wrong@99999", DEV_A)).status_code == 401
        locked = await post(c, TEST_PASSWORD, DEV_A)
        assert locked.status_code == 429 and 55 <= locked.json()["details"]["retry_after"] <= 60
        # An id that isn't the right shape is ignored (falls into the no-device bucket).
        assert (await post(c, "Wrong@99999", "x y")).status_code == 401
        assert (await post(c, TEST_PASSWORD, DEV_B, ip="198.51.100.4")).status_code == 200


async def test_device_id_shape_is_validated():
    assert rate_limit.valid_device_id("abcdefghijklmnop") == "abcdefghijklmnop"
    assert rate_limit.valid_device_id("short") is None
    assert rate_limit.valid_device_id("a" * 65) is None
    assert rate_limit.valid_device_id("has space in it 123456") is None
    assert rate_limit.valid_device_id(None) is None


async def test_legacy_account_lock_fields_no_longer_block_anyone(db, cleanup):
    user = await _customer(db, cleanup)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"login_locked_until": datetime.now(timezone.utc) + timedelta(hours=1), "failed_login_attempts": 4}},
    )
    assert (await AuthService(db).login(user["email"], TEST_PASSWORD))["user"] is not None
    fresh = await db.users.find_one({"_id": user["_id"]})
    assert "login_locked_until" not in fresh and "failed_login_attempts" not in fresh
