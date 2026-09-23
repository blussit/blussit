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
from app.core.exceptions import BadRequestException, ForbiddenException


def ensure_own_center(actor_role: str, actor_center_id: str | None, service_center_id: str) -> None:
    """Admins bypass this — they're meant to act platform-wide. Everyone
    else (manager, captain) must have a matching service_center_id."""
    if actor_role == "admin":
        return
    if actor_center_id != service_center_id:
        raise ForbiddenException("You don't have access to this service center")


def resolve_grant_center_id(actor_role: str, actor_center_id: str | None, payload_center_id: str | None) -> str:
    """Which center a manager/admin-issued subscription grant (assign, or a
    'sell a plan' cash/link/autopay offer) gets attributed to — the ONLY
    thing that makes it show up on a manager's own Subscriptions page (see
    UserSubscriptionService.center_overview). A manager is always
    attributed to THEIR OWN center, never a payload-supplied one — trusting
    that would let one manager silently grant a plan under another
    center's name. An admin has no center of their own, so they must
    supply one explicitly.

    Raises rather than silently granting with no center at all: a
    subscription created that way exists in the database but never
    appears anywhere for a manager to find it — a real incident this
    guard exists to make impossible going forward."""
    if actor_role == "manager":
        if not actor_center_id:
            raise BadRequestException(
                "Your manager account isn't linked to a service center yet — ask an admin to link one before selling or assigning a plan."
            )
        return actor_center_id
    if payload_center_id:
        return payload_center_id
    raise BadRequestException("Pick a service center for this plan.")
