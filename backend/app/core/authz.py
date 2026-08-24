"""
Center-scoping authorization — shared across every service that takes a
service_center_id as a path/query param.

Without this, a manager-facing route gated only by role
(require_manager_or_admin) trusts whatever service_center_id the caller
passes, letting any manager view or modify ANY other center's data via a
direct API call — not just their own store's. Every such service method
should call ensure_own_center right after it knows both the acting user's
role/center and the service_center_id being acted on.

Takes primitive args (not the CurrentUser class) so the service layer
doesn't need to import from app.core.dependencies (the HTTP/auth layer) —
keeps the dependency direction one-way.
"""
from app.core.exceptions import ForbiddenException


def ensure_own_center(actor_role: str, actor_center_id: str | None, service_center_id: str) -> None:
    """Admins bypass this — they're meant to act platform-wide. Everyone
    else (manager, captain) must have a matching service_center_id."""
    if actor_role == "admin":
        return
    if actor_center_id != service_center_id:
        raise ForbiddenException("You don't have access to this service center")
