"""
Reusable FastAPI dependencies: database access, current-user resolution
from JWT, role-based access guards, and common pagination/query params.
"""
from typing import Optional

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

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
        # Embedded in the JWT at login (see AuthService._issue_tokens) — a
        # manager/captain whose center changes needs to re-login for this to
        # update, same accepted tradeoff as must_change_password's frontend
        # staleness. Deliberately not a per-request DB lookup: this claim is
        # read on every single center-scoped request, and this app is meant
        # to scale to many concurrent managers.
        self.service_center_id = service_center_id


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedException("Missing authentication token")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise UnauthorizedException(str(exc)) from exc

    if payload.get("type") != "access":
        raise UnauthorizedException("Invalid token type")

    return CurrentUser(
        id=payload["sub"],
        role=payload.get("role", UserRole.CUSTOMER.value),
        email=payload.get("email"),
        phone=payload.get("phone"),
        service_center_id=payload.get("service_center_id"),
    )


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
        page: int = Query(1, ge=1),
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
