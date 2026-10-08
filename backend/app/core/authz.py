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
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException

# Account standing. "inactive" and "suspended" switch an account off: refused
# at login, on every access-token use and on refresh (CAP-06 — "inactive"
# used to block nothing). "pending" is NOT one of them — it is an onboarding
# state (a new captain must still sign in to finish KYC); "active" and a
# missing status (legacy rows default to active) are normal.
_SWITCHED_OFF = {
    "inactive": "Your account is inactive. Contact support.",
    "suspended": "Your account has been suspended. Contact support.",
}


def account_switched_off(user: dict | None) -> str | None:
    """The refusal message when this account may not be used, else None."""
    return _SWITCHED_OFF.get((user or {}).get("status") or "")


def ensure_own_center(actor_role: str, actor_center_id: str | None, service_center_id: str | None) -> None:
    """Admins bypass this — they're meant to act platform-wide. Everyone
    else (manager, captain) must have a matching service_center_id.

    Fails CLOSED on a missing center on either side: a manager whose
    account isn't linked to a center yet (None) used to "match" every
    object that has no center either (None == None) — an unlinked manager
    could reach unassigned captains' KYC, wallet and GPS. A center-scoped
    object with no center belongs to the admin."""
    if actor_role == "admin":
        return
    if not actor_center_id or not service_center_id or actor_center_id != service_center_id:
        raise ForbiddenException("You don't have access to this service center")


def manager_center_or_raise(actor_role: str, actor_center_id: str | None) -> str | None:
    """The center a report/list is scoped to for this actor: a manager is
    ALWAYS their own (never a client-supplied param), and a manager with no
    center linked gets nothing rather than the platform-wide view an
    unscoped (None) query would return. Admin -> None (caller decides)."""
    if actor_role == "admin":
        return None
    if not actor_center_id:
        raise ForbiddenException("Your account isn't linked to a service center yet — ask an admin to link one.")
    return actor_center_id


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


async def customers_known_to_center(db, customer_ids: list[str], center_id: str) -> set[str]:
    """Which of these customers a center has dealt with: a booking there, a
    plan its manager granted/sold, or a society enrolment in one of its
    societies. THE "customer known to my center" rule — the 360 view, the
    typeahead, the customer password reset and every other staff read of a
    customer's data by id use this one function."""
    known: set[str] = set(
        await db.bookings.distinct("customer_id", {"customer_id": {"$in": customer_ids}, "service_center_id": center_id})
    )
    rest = [c for c in customer_ids if c not in known]
    if rest:
        known.update(await db.user_subscriptions.distinct(
            "customer_id", {"customer_id": {"$in": rest}, "service_center_id": center_id},
        ))
    rest = [c for c in customer_ids if c not in known]
    if rest:
        society_ids = [str(s["_id"]) for s in await db.societies.find({"service_center_id": center_id}, {"_id": 1}).to_list(length=500)]
        if society_ids:
            known.update(await db.society_enrollments.distinct(
                "customer_id", {"customer_id": {"$in": rest}, "society_id": {"$in": society_ids}},
            ))
    return known


async def ensure_customer_in_scope(db, actor_role: str, actor_center_id: str | None, customer_id: str) -> None:
    """Gate for a staff endpoint that reads one customer's data by id
    (addresses, plans, 360 view, password reset): an admin passes; a
    manager only for a customer known to THEIR center (AZ-01 / MGR-04 —
    any manager could read any customer's saved addresses and plans by
    id). Anyone else, an unlinked manager, or an unknown id: 404, never
    403 — an id outside the center must not confirm it exists."""
    if actor_role == "admin":
        return
    if actor_role != "manager" or not actor_center_id or customer_id not in await customers_known_to_center(db, [customer_id], actor_center_id):
        raise NotFoundException("Customer not found")
