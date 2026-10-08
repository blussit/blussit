"""
Reusable FastAPI dependencies: database access, current-user resolution
from JWT, role-based access guards, and common pagination/query params.
"""
from typing import Optional

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from bson.errors import InvalidId

from app.core.authz import account_switched_off
from app.core.config import settings
from app.core.database import get_database
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_token
from app.models.enums import UserRole

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


class CurrentUser:
    def __init__(
        self,
        id: str,
        role: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        service_center_id: Optional[str] = None,
    ):
        self.id = id
        self.role = role
        self.email = email
        self.phone = phone
        self.service_center_id = service_center_id


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedException("Missing authentication token")
    return await resolve_access_token(credentials.credentials)


async def resolve_access_token(token: str) -> CurrentUser:
    """Access token -> the CURRENT user record. Shared by every REST route
    (get_current_user) and the WebSocket endpoint, so both enforce the same
    revocation rules: deleted / suspended / inactive accounts
    (authz.account_switched_off), token_version (logout,
    password change/reset), and any change of role or center since the
    token was minted. Role and center always come from the DB row, never
    from the token's claims."""
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise UnauthorizedException(str(exc)) from exc

    if payload.get("type") != "access":
        raise UnauthorizedException("Invalid token type")

    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise UnauthorizedException("Invalid token subject")
    try:
        user = await get_database().users.find_one({"_id": ObjectId(user_id)})
    except (InvalidId, TypeError):
        raise UnauthorizedException("Invalid token subject") from None
    if not user or user.get("is_deleted") or account_switched_off(user):
        raise UnauthorizedException("Your account is no longer active")
    if payload.get("tv", 0) != user.get("token_version", 0):
        raise UnauthorizedException("Session expired — please log in again.")
    if payload.get("role") != user.get("role"):
        raise UnauthorizedException("Session permissions changed — please log in again.")
    if payload.get("service_center_id") != user.get("service_center_id"):
        raise UnauthorizedException("Session scope changed — please log in again.")

    return CurrentUser(
        id=user_id,
        role=user["role"],
        email=user.get("email"),
        phone=user.get("phone"),
        service_center_id=user.get("service_center_id"),
    )


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser | None:
    """Who's calling, IF anyone — for routes that are genuinely public but
    can attach context when the caller happens to be signed in (e.g. a
    plan enquiry from a logged-in customer). A bad or expired token is
    treated as "not signed in" rather than an error: the route works either
    way, so failing it would only break the public case."""
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials)
    except Exception:
        return None


async def get_catalogue_viewer(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser | None:
    """Who is reading a public catalogue endpoint, IF anyone. A stale token
    reads as anonymous — the landing page must never bounce a visitor with
    an old session to /login — EXCEPT when the caller explicitly asked for
    the switched-off rows (only admin screens do): then the 401 makes the
    app refresh its session and retry, instead of quietly getting the
    public view (no captain_fee) that the admin's next save would write
    back as "cleared"."""
    if credentials is None:
        return None
    try:
        return await resolve_access_token(credentials.credentials)
    except UnauthorizedException:
        if any(request.query_params.get(k, "").lower() in ("false", "0", "no", "off") for k in ("active_only", "featured_only")):
            raise
        return None


def is_catalogue_editor(user: CurrentUser | None) -> bool:
    """Admin/manager — the only callers a public catalogue endpoint shows
    switched-off rows (active_only=false) or internal fields (a service's
    captain_fee) to. Everyone else, signed in or not, gets the public view."""
    return bool(user and user.role in (UserRole.ADMIN.value, UserRole.MANAGER.value))


def require_roles(*roles: UserRole):
    """Dependency factory enforcing RBAC on a route."""

    async def _checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        allowed = {r.value for r in roles}
        if current_user.role not in allowed:
            raise ForbiddenException("You do not have permission to perform this action")
        return current_user

    return _checker


require_customer = require_roles(UserRole.CUSTOMER)
require_captain = require_roles(UserRole.CAPTAIN)
require_manager = require_roles(UserRole.MANAGER)
require_admin = require_roles(UserRole.ADMIN)
require_manager_or_admin = require_roles(UserRole.MANAGER, UserRole.ADMIN)
require_staff = require_roles(UserRole.CAPTAIN, UserRole.MANAGER, UserRole.ADMIN)
require_any = require_roles(UserRole.CUSTOMER, UserRole.CAPTAIN, UserRole.MANAGER, UserRole.ADMIN)


class PaginationParams:
    def __init__(
        self,
        # Bounded: an absurd page made skip = (page-1)*page_size overflow
        # Mongo's 64-bit skip and answered 500. No list here is browsed
        # anywhere near 10,000 pages deep.
        page: int = Query(1, ge=1, le=10_000),
        page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
        search: Optional[str] = Query(None),
        sort_by: str = Query("created_at"),
        sort_order: int = Query(-1, description="1 for ascending, -1 for descending"),
    ):
        self.page = page
        self.page_size = page_size
        self.search = search
        self.sort_by = sort_by
        self.sort_order = 1 if sort_order == 1 else -1
