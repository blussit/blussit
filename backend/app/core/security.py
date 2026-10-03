"""
Password hashing and JWT access/refresh token utilities.

bcrypt costs ~250 ms of CPU. Async request handlers must use the *_async
variants, which run it on a worker thread — calling the sync ones inline
freezes every request and WebSocket on the instance while it hashes. The
sync versions remain for seed data and scripts.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    # OTP/Google-only accounts store no password; malformed legacy hashes
    # count as a mismatch too — never an error.
    if not hashed_password:
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        return False


async def hash_password_async(plain_password: str) -> str:
    return await asyncio.to_thread(hash_password, plain_password)


async def verify_password_async(plain_password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)


def create_token(subject: str, role: str, token_type: TokenType, extra_claims: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    if token_type == TokenType.ACCESS:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type.value,
        "iat": now,
        "exp": expire,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str, role: str, extra_claims: dict[str, Any] | None = None) -> str:
    return create_token(subject, role, TokenType.ACCESS, extra_claims)


def create_refresh_token(subject: str, role: str, token_version: int = 0, family: str | None = None) -> str:
    # tv lets a password change (or forced logout) invalidate every
    # refresh token issued before it — see AuthService.refresh.
    # jti makes each refresh token single-use (rotation with reuse
    # detection); fam ties a login's chain of rotated tokens together so a
    # replayed one can kill exactly that chain (one device's session).
    return create_token(
        subject, role, TokenType.REFRESH,
        {"tv": token_version, "jti": uuid.uuid4().hex, "fam": family or uuid.uuid4().hex},
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
