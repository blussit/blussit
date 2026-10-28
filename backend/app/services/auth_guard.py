"""
Abuse controls for the public auth endpoints, kept in two small collections
that every Cloud Run instance shares (so, unlike core/rate_limit.py, they are
global):

  rate_counters  fixed-window counters and per-bucket login state, TTL on
                 `expires_at`;
  abuse_holds    escalating OTP "holds" per abusive source, TTL on
                 `expires_at` (a hold is remembered for a while after it ends
                 so the next violation is longer).

What lives here:
  * login throttling per (account + device + source IP) — never account-wide,
    so a stranger can't lock the owner out; devices that signed in before are
    never blocked (trusted devices);
  * OTP source limits: per device and per IP requests/hour with escalating
    holds (5 min -> 30 min -> 2 h -> 24 h);
  * the platform-wide OTP message budget and its once-only alert.

Every number is a Settings value with a default that works unchanged
(app/core/config.py). Nothing here stores a raw IP or device id: keys are
hashed.
"""
import hashlib
import hmac
import logging
import math
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.exceptions import TooManyRequestsException

logger = logging.getLogger(__name__)

COUNTERS = "rate_counters"
HOLDS = "abuse_holds"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def key_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def device_hash(device_id: str | None) -> str | None:
    """What is stored for a device id: keyed so a database reader can't replay
    it as a header."""
    if not device_id:
        return None
    return hmac.new(settings.JWT_SECRET_KEY.encode(), b"device:" + device_id.encode(), hashlib.sha256).hexdigest()[:24]


def throttled(message: str, retry_after: float, code: str = "RATE_LIMITED") -> TooManyRequestsException:
    seconds = max(1, int(math.ceil(retry_after)))
    exc = TooManyRequestsException(message, {"retry_after": seconds})
    exc.error_code = code
    return exc


def human_wait(seconds: float) -> str:
    seconds = max(1, int(math.ceil(seconds)))
    if seconds < 90:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = math.ceil(seconds / 60)
    if minutes < 120:
        return f"{minutes} minutes"
    hours = math.ceil(minutes / 60)
    return f"{hours} hours"


def _parse_pairs(spec: str, default: list[tuple[int, int]]) -> list[tuple[int, int]]:
    try:
        pairs = sorted((int(a), int(b)) for a, b in (item.split(":") for item in spec.split(",") if item.strip()))
        return pairs or default
    except ValueError:
        logger.error("Bad throttle steps %r — using the defaults", spec)
        return default


def _parse_seconds(spec: str, default: list[int]) -> list[int]:
    try:
        values = [int(x) for x in spec.split(",") if x.strip()]
        return values or default
    except ValueError:
        logger.error("Bad hold steps %r — using the defaults", spec)
        return default


# ---------------------------------------------------------------- counters


async def window_hit(db: AsyncIOMotorDatabase, key: str, window_seconds: int, now: datetime | None = None) -> tuple[int, datetime]:
    """Counts one event in a fixed window; returns (count including this one,
    when the window ends). Atomic across instances."""
    now = now or utcnow()
    coll = db[COUNTERS]
    for _ in range(3):
        doc = await coll.find_one_and_update(
            {"_id": key, "expires_at": {"$gt": now}}, {"$inc": {"n": 1}}, return_document=ReturnDocument.AFTER
        )
        if doc:
            return int(doc.get("n", 1)), aware(doc["expires_at"])
        end = now + timedelta(seconds=window_seconds)
        try:
            # Over a stale (not yet TTL-swept) window, or none at all; a live
            # one makes the upsert collide instead.
            await coll.update_one({"_id": key, "expires_at": {"$lte": now}}, {"$set": {"n": 1, "expires_at": end}}, upsert=True)
            return 1, end
        except DuplicateKeyError:
            continue
    return 1, now + timedelta(seconds=window_seconds)


async def window_count(db: AsyncIOMotorDatabase, key: str, now: datetime | None = None) -> int:
    now = now or utcnow()
    doc = await db[COUNTERS].find_one({"_id": key, "expires_at": {"$gt": now}}, {"n": 1})
    return int((doc or {}).get("n", 0))


# ------------------------------------------------------------ OTP sources


def _otp_sources(ip: str | None, device: str | None) -> list[tuple[str, str, int]]:
    """(scope, value, allowed per hour). A request with a valid X-Device-Id is
    counted per device AND per IP; one without (a script) is counted by its IP
    with the strict per-device number — the real frontend always sends one."""
    sources: list[tuple[str, str, int]] = []
    if device:
        sources.append(("dev", device, settings.OTP_SOURCE_HOURLY_LIMIT))
    elif ip:
        sources.append(("nodev", ip, settings.OTP_SOURCE_HOURLY_LIMIT))
    if ip:
        sources.append(("ip", ip, settings.OTP_IP_HOURLY_LIMIT))
    return sources


async def check_otp_holds(db: AsyncIOMotorDatabase, ip: str | None, device: str | None, now: datetime | None = None) -> None:
    """Refuses (429 + retry_after) while any of this request's sources is on hold."""
    now = now or utcnow()
    for scope, value, _limit in _otp_sources(ip, device):
        hold = await db[HOLDS].find_one({"_id": f"otp:{scope}:{key_hash(value)}", "until": {"$gt": now}})
        if hold:
            wait = (aware(hold["until"]) - now).total_seconds()
            raise throttled(f"Too many code requests from this device — try again in {human_wait(wait)}.", wait, "OTP_HELD")


async def count_otp_request(db: AsyncIOMotorDatabase, ip: str | None, device: str | None, now: datetime | None = None) -> None:
    """Counts one code request per source for the hour; the one past a
    source's allowance starts (or lengthens) that source's hold."""
    now = now or utcnow()
    steps = _parse_seconds(settings.OTP_HOLD_STEPS_SECONDS, [300, 1800, 7200, 86400])
    for scope, value, limit in _otp_sources(ip, device):
        counter = f"otp-src:{scope}:{key_hash(value)}"
        n, _end = await window_hit(db, counter, 3600, now)
        if n <= limit:
            continue
        hold_id = f"otp:{scope}:{key_hash(value)}"
        previous = await db[HOLDS].find_one({"_id": hold_id, "expires_at": {"$gt": now}}, {"level": 1})
        level = min(int((previous or {}).get("level", 0)) + 1, len(steps))
        seconds = steps[level - 1]
        until = now + timedelta(seconds=seconds)
        await db[HOLDS].update_one(
            {"_id": hold_id},
            {"$set": {"level": level, "until": until, "scope": scope, "expires_at": until + timedelta(seconds=settings.OTP_HOLD_MEMORY_SECONDS)}},
            upsert=True,
        )
        await db[COUNTERS].delete_one({"_id": counter})
        logger.warning("OTP hold level %d (%ds) on %s source %s", level, seconds, scope, key_hash(value))
        raise throttled(f"Too many code requests from this device — try again in {human_wait(seconds)}.", seconds, "OTP_HELD")


# ----------------------------------------------------------- global budget


def _budget_keys(now: datetime) -> tuple[str, str]:
    return f"otp-budget:h:{now:%Y%m%d%H}", f"otp-budget:d:{now:%Y%m%d}"


async def budget_over(db: AsyncIOMotorDatabase, now: datetime | None = None) -> bool:
    """Is the platform already past its hourly or daily message budget?"""
    now = now or utcnow()
    hourly, daily = _budget_keys(now)
    return (
        await window_count(db, hourly, now) >= settings.OTP_GLOBAL_HOURLY_BUDGET
        or await window_count(db, daily, now) >= settings.OTP_GLOBAL_DAILY_BUDGET
    )


async def budget_count(db: AsyncIOMotorDatabase, now: datetime | None = None) -> str | None:
    """Counts one OTP message (WhatsApp or SMS). Returns "hourly"/"daily" the
    FIRST time that budget is crossed (once per window — the caller alerts),
    else None."""
    now = now or utcnow()
    hourly_key, daily_key = _budget_keys(now)
    hourly, _ = await window_hit(db, hourly_key, 3700, now)
    daily, _ = await window_hit(db, daily_key, 86500, now)
    crossed = None
    if hourly == settings.OTP_GLOBAL_HOURLY_BUDGET + 1:
        crossed = "hourly"
    elif daily == settings.OTP_GLOBAL_DAILY_BUDGET + 1:
        crossed = "daily"
    if crossed:
        logger.error(
            "OTP message budget crossed (%s): %d this hour, %d today (limits %d/h, %d/day) — new sources are slowed down",
            crossed, hourly, daily, settings.OTP_GLOBAL_HOURLY_BUDGET, settings.OTP_GLOBAL_DAILY_BUDGET,
        )
    return crossed


async def over_budget_gate(db: AsyncIOMotorDatabase, ip: str | None, device: str | None, now: datetime | None = None) -> None:
    """While over budget a NEW code (not a resend of a live one) is allowed
    once per cooldown per source — existing flows carry on."""
    now = now or utcnow()
    who = device or ip or "-"
    n, end = await window_hit(db, f"otp-newsrc:{key_hash(who)}", settings.OTP_OVER_BUDGET_COOLDOWN_SECONDS, now)
    if n > 1:
        wait = (end - now).total_seconds()
        raise throttled(f"We're busy right now — try again in {human_wait(wait)}.", wait, "OTP_BUSY")


# ----------------------------------------------------------------- login


def _login_steps() -> list[tuple[int, int]]:
    return _parse_pairs(settings.LOGIN_THROTTLE_STEPS, [(5, 60), (10, 300), (20, 1800)])


def login_bucket_id(account: str, device: str | None, ip: str | None) -> str:
    return "login:" + key_hash(account, device or "-", ip or "-")


async def login_gate(
    db: AsyncIOMotorDatabase, account: str, device: str | None, ip: str | None, trusted: bool, now: datetime | None = None,
) -> float:
    """Before the password is even checked. Raises 429 + retry_after while THIS
    (account, device, IP) bucket is locked or this IP is blocked for stuffing —
    unless the device has signed in to this account before. Returns the extra
    delay (seconds) an unknown device pays while the account is under attack
    from elsewhere (it slows, never blocks)."""
    now = now or utcnow()
    if trusted:
        return 0.0
    bucket = await db[COUNTERS].find_one({"_id": login_bucket_id(account, device, ip), "expires_at": {"$gt": now}})
    locked = aware((bucket or {}).get("locked_until"))
    if locked and locked > now:
        wait = (locked - now).total_seconds()
        raise throttled(f"Too many attempts — try again in {human_wait(wait)}.", wait, "LOGIN_THROTTLED")
    if ip:
        ip_doc = await db[COUNTERS].find_one({"_id": f"login-ip:{key_hash(ip)}", "expires_at": {"$gt": now}})
        blocked = aware((ip_doc or {}).get("blocked_until"))
        if blocked and blocked > now:
            wait = (blocked - now).total_seconds()
            raise throttled(f"Too many attempts from this network — try again in {human_wait(wait)}.", wait, "LOGIN_THROTTLED")
    if await window_count(db, f"login-acct:{key_hash(account)}", now) >= settings.LOGIN_ACCOUNT_SLOWDOWN_FAILS:
        return max(0.0, float(settings.LOGIN_ACCOUNT_SLOWDOWN_SECONDS))
    return 0.0


async def login_failed(
    db: AsyncIOMotorDatabase, account: str, device: str | None, ip: str | None, now: datetime | None = None,
) -> None:
    """Records a wrong password: the bucket's escalating lock, the account-wide
    slow-down counter and the per-IP credential-stuffing counter."""
    now = now or utcnow()
    window = timedelta(minutes=settings.LOGIN_FAIL_WINDOW_MINUTES)
    bucket_id = login_bucket_id(account, device, ip)
    doc = await db[COUNTERS].find_one_and_update(
        {"_id": bucket_id}, {"$inc": {"fails": 1}, "$set": {"expires_at": now + window}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    fails = int(doc.get("fails", 1))
    seconds = 0
    for threshold, lock in _login_steps():
        if fails >= threshold:
            seconds = lock
    if seconds:
        until = now + timedelta(seconds=seconds)
        await db[COUNTERS].update_one(
            {"_id": bucket_id}, {"$set": {"locked_until": until, "expires_at": max(until, now + window)}}
        )
    await window_hit(db, f"login-acct:{key_hash(account)}", settings.LOGIN_FAIL_WINDOW_MINUTES * 60, now)
    if ip:
        n, end = await window_hit(db, f"login-ip:{key_hash(ip)}", settings.LOGIN_IP_WINDOW_SECONDS, now)
        if n > settings.LOGIN_IP_FAIL_CEILING:
            until = now + timedelta(seconds=settings.LOGIN_IP_BLOCK_SECONDS)
            await db[COUNTERS].update_one(
                {"_id": f"login-ip:{key_hash(ip)}"}, {"$set": {"blocked_until": until, "expires_at": max(end, until)}}
            )
            logger.warning("Login credential-stuffing guard: IP bucket %s blocked for %ds", key_hash(ip), settings.LOGIN_IP_BLOCK_SECONDS)


async def login_succeeded(db: AsyncIOMotorDatabase, account: str, device: str | None, ip: str | None) -> None:
    await db[COUNTERS].delete_one({"_id": login_bucket_id(account, device, ip)})
