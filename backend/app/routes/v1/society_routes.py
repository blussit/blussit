"""
Society plans HTTP surface — see docs/SOCIETY_PLANS.md §4 for who may do
what. Every manager route re-checks the society's center against the
caller's (ensure_own_center); captains only ever reach societies where they
are today's captain; the public form is keyed by an unguessable token and
never returns anyone's data but the caller's own.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_optional_user,
    require_admin,
    require_captain,
    require_customer,
    require_manager_or_admin,
)
from app.core.exceptions import BadRequestException
from app.core.responses import success
from app.schemas.society_schema import (
    ActivateEnrollmentRequest,
    ArriveRequest,
    CancelEnrollmentRequest,
    CouponPreviewRequest,
    ManagerEnrollRequest,
    PremiumBookingRequest,
    RateCardRequest,
    SocietyCaptainRequest,
    SocietyCreateRequest,
    SocietyEnrollRequest,
    SocietyPaymentLinkRequest,
    SocietyPlanRequest,
    SocietyPlanUpdateRequest,
    SocietyQuoteRequest,
    SocietyUpdateRequest,
    WashedRequest,
)
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService
from app.services.society_service import SocietyService

form_router = APIRouter(prefix="/society-forms", tags=["Society form (public)"])
society_router = APIRouter(prefix="/societies", tags=["Societies"])
enrollment_router = APIRouter(prefix="/society-enrollments", tags=["Society enrollments"], dependencies=[Depends(require_manager_or_admin)])
plan_router = APIRouter(prefix="/society-plans", tags=["Society plans"], dependencies=[Depends(require_admin)])


async def _audit(db, user: CurrentUser, action: str, collection: str, ref: str, details: dict | None = None) -> None:
    await AuditService(db).log_action(user.id, user.role, action, collection, ref, details)


def _customer_id(user: CurrentUser | None) -> str | None:
    return user.id if user is not None and user.role == "customer" else None


# ---------------------------------------------------------------------------
# Public resident form
# ---------------------------------------------------------------------------


@form_router.get("/{token}")
async def society_form(token: str, current_user: CurrentUser | None = Depends(get_optional_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The form's static data: society name, the plans offered here (plus a
    signed-in resident's personal plan), customise options, car types."""
    service = SocietyService(db)
    society = await service.society_by_token(token)
    return success(await service.public_form(society, _customer_id(current_user)))


@form_router.post("/{token}/quote")
async def society_form_quote(
    token: str, payload: SocietyQuoteRequest,
    current_user: CurrentUser | None = Depends(get_optional_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = SocietyService(db)
    society = await service.society_by_token(token)
    return success(await service.quote(society, payload, payload.vehicle_types, _customer_id(current_user)))


@form_router.post("/{token}/enroll")
async def society_form_enroll(
    token: str, payload: SocietyEnrollRequest,
    current_user: CurrentUser | None = Depends(get_optional_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Phone proven by OTP (or already signed in with that phone) -> the
    resident's request is saved and they're signed in, so the same page can
    take payment and become their society hub."""
    from datetime import datetime, timezone

    from app.services.auth_service import AuthService

    service = SocietyService(db)
    society = await service.society_by_token(token)
    auth = AuthService(db)
    tokens = None
    if current_user is not None and current_user.role == "customer" and current_user.phone == payload.phone:
        customer = await auth.users.find_by_id(current_user.id)
    else:
        # Refuse what we can before spending the one-time code.
        quoted = await service.quote(society, payload, [c.vehicle_type for c in payload.cars], None if not payload.plan_id else _customer_id(current_user))
        if quoted.get("coupon") and not quoted["coupon"]["valid"]:
            raise BadRequestException(quoted["coupon"]["error"] or "That coupon can't be used.")
        await auth.require_phone_proof(payload.phone, payload.phone_otp, payload.phone_access_token)
        customer = await auth.ensure_customer_by_phone(payload.phone, payload.resident_name)
        await auth.users.update_by_id(
            str(customer["_id"]), {"phone_verified": True, "phone_verified_at": datetime.now(timezone.utc), "last_login_at": datetime.now(timezone.utc)}
        )
        customer = await auth.users.find_by_id(str(customer["_id"]))
        tokens = auth._issue_tokens(customer)
    if not customer:
        raise BadRequestException("Sign in again to continue.")
    enrollment = await service.enroll(society, payload, customer, source="form")
    return success({"enrollment": enrollment, "auth": tokens}, "Request saved")


@form_router.get("/{token}/me", dependencies=[Depends(require_customer)])
async def society_form_me(token: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The signed-in resident's own plan in this society — nobody else's."""
    service = SocietyService(db)
    society = await service.society_by_token(token)
    return success(await service.my_hub(society, current_user.id))


@form_router.post("/{token}/me/enrollments/{enrollment_id}/withdraw", dependencies=[Depends(require_customer)])
async def society_form_withdraw(token: str, enrollment_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    from app.core.exceptions import NotFoundException

    service = SocietyService(db)
    society = await service.society_by_token(token)
    enrollment = await service.get_enrollment(enrollment_id)
    if enrollment.get("customer_id") != current_user.id or enrollment.get("society_id") != str(society["_id"]):
        raise NotFoundException("Enrollment not found")
    if enrollment.get("status") not in ("requested", "awaiting_payment"):
        raise BadRequestException("An active plan is cancelled by the society manager — please call them.")
    result = await service.cancel(enrollment, vehicle_ids=None, actor_id=current_user.id, reason="Withdrawn by resident")
    await PaymentService(db).void_society_links(enrollment_id, "request withdrawn")
    return success(result, "Request withdrawn")


@form_router.post("/{token}/me/enrollments/{enrollment_id}/coupon-preview", dependencies=[Depends(require_customer)])
async def society_form_coupon_preview(
    token: str, enrollment_id: str, payload: CouponPreviewRequest,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The resident's own renewal (or open request) with a coupon: what it
    takes off. Their own enrollment only."""
    from app.core.exceptions import NotFoundException

    service = SocietyService(db)
    society = await service.society_by_token(token)
    enrollment = await service.get_enrollment(enrollment_id)
    if enrollment.get("customer_id") != current_user.id or enrollment.get("society_id") != str(society["_id"]):
        raise NotFoundException("Enrollment not found")
    return success(await service.enrollment_coupon_preview(enrollment, payload.coupon_code, payload.renewal))


# ---------------------------------------------------------------------------
# Resident (signed in)
# ---------------------------------------------------------------------------


@society_router.post("/my/premium-bookings", dependencies=[Depends(require_customer)])
async def my_premium_booking(payload: PremiumBookingRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyService(db).book_premium(payload, actor_id=current_user.id, actor_role="customer", actor_center_id=None)
    return success(result, "Premium wash booked")


# ---------------------------------------------------------------------------
# Captain
# ---------------------------------------------------------------------------


@society_router.get("/captain/today", dependencies=[Depends(require_captain)])
async def captain_societies_today(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyService(db).captain_today(current_user.id))


@society_router.post("/captain/{society_id}/arrive", dependencies=[Depends(require_captain)])
async def captain_arrive(society_id: str, payload: ArriveRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyService(db).arrive(current_user.id, society_id, payload), "Arrival marked")


@society_router.put("/captain/{society_id}/washed", dependencies=[Depends(require_captain)])
async def captain_washed(society_id: str, payload: WashedRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyService(db).set_washed(current_user.id, society_id, payload.vehicle_ids), "Saved")


# ---------------------------------------------------------------------------
# Manager / admin
# ---------------------------------------------------------------------------


def _scope_center(user: CurrentUser, center_id: Optional[str]) -> Optional[str]:
    """A manager always sees their own center; an admin may filter by one."""
    if user.role == "manager":
        if not user.service_center_id:
            raise BadRequestException("Your account isn't linked to a service center yet.")
        return user.service_center_id
    return center_id or None


@society_router.get("", dependencies=[Depends(require_manager_or_admin)])
async def list_societies(
    center_id: Optional[str] = None, search: Optional[str] = None, month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    return success(await SocietyService(db).list_societies(center_id=_scope_center(current_user, center_id), search=search, month=month))


@society_router.post("", dependencies=[Depends(require_manager_or_admin)])
async def create_society(payload: SocietyCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyService(db).create_society(payload, current_user.id, current_user.role, current_user.service_center_id)
    await _audit(db, current_user, "CREATE_SOCIETY", "societies", result["id"])
    return success(result, "Society registered")


@society_router.get("/captains", dependencies=[Depends(require_manager_or_admin)])
async def society_captains(center_id: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    center = _scope_center(current_user, center_id)
    if not center:
        raise BadRequestException("Pick a service center.")
    return success(await SocietyService(db).captains_for_center(center))


@society_router.get("/{society_id}", dependencies=[Depends(require_manager_or_admin)])
async def get_society(society_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    detail = await service.society_detail(society)
    detail["payments"] = await service.payments_for(society)
    return success(detail)


@society_router.put("/{society_id}", dependencies=[Depends(require_manager_or_admin)])
async def update_society(society_id: str, payload: SocietyUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    result = await service.update_society(society, payload)
    await _audit(db, current_user, "UPDATE_SOCIETY", "societies", society_id, payload.model_dump(exclude_unset=True))
    return success(result, "Saved")


@society_router.post("/{society_id}/rotate-link", dependencies=[Depends(require_manager_or_admin)])
async def rotate_society_link(society_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    result = await service.rotate_link(society)
    await _audit(db, current_user, "ROTATE_SOCIETY_LINK", "societies", society_id)
    return success(result, "New link created — the old one no longer works")


@society_router.put("/{society_id}/captain", dependencies=[Depends(require_manager_or_admin)])
async def set_society_captain(society_id: str, payload: SocietyCaptainRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    result = await service.set_captain(society, payload.captain_id, payload.date)
    await _audit(db, current_user, "SET_SOCIETY_CAPTAIN", "societies", society_id, payload.model_dump())
    return success(result, "Captain saved")


@society_router.get("/{society_id}/enrollments", dependencies=[Depends(require_manager_or_admin)])
async def society_enrollments(
    society_id: str, status: Optional[str] = Query(None, pattern=r"^(open|requested|awaiting_payment|active|cancelled)$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    rows = await service.enrollments_for_society(society, status)
    # Each resident's unpaid WhatsApp link (if any), so the manager sees
    # "link sent · waiting" instead of guessing. One query for the page.
    links = await db.payment_orders.find(
        {"kind": "link", "purpose": "society", "status": "created", "society_enrollment_id": {"$in": [r["id"] for r in rows]}},
        {"society_enrollment_id": 1, "society_renewal": 1, "short_url": 1, "amount_paise": 1, "created_at": 1},
    ).sort("created_at", -1).to_list(length=500)
    latest: dict = {}
    for link in links:
        latest.setdefault(link["society_enrollment_id"], {
            "short_url": link.get("short_url"), "amount": (link.get("amount_paise") or 0) / 100,
            "renewal": bool(link.get("society_renewal")), "sent_at": link["created_at"].isoformat() if link.get("created_at") else None,
        })
    for r in rows:
        r["payment_link"] = latest.get(r["id"])
    return success(rows)


@society_router.get("/{society_id}/plans", dependencies=[Depends(require_manager_or_admin)])
async def society_plans_offered(
    society_id: str, phone: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """What the manager's 'add resident' form offers — the society's plans,
    plus a personal plan for the typed phone if admin made one."""
    from app.utils.phone import validate_indian_mobile

    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    customer_id = None
    normalized = validate_indian_mobile(phone or "")
    if normalized:
        user = await db.users.find_one({"phone": normalized, "role": "customer", "is_deleted": {"$ne": True}}, {"_id": 1})
        customer_id = str(user["_id"]) if user else None
    return success(await service.public_form(society, customer_id))


@society_router.post("/{society_id}/quote", dependencies=[Depends(require_manager_or_admin)])
async def society_staff_quote(society_id: str, payload: SocietyQuoteRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    return success(await service.quote(society, payload, payload.vehicle_types, None))


@society_router.post("/{society_id}/enrollments", dependencies=[Depends(require_manager_or_admin)])
async def add_society_resident(society_id: str, payload: ManagerEnrollRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    from app.services.auth_service import AuthService

    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    customer = await AuthService(db).ensure_customer_by_phone(payload.phone, payload.resident_name)
    payload.pay_now = False
    enrollment = await service.enroll(society, payload, customer, source="manager", actor_id=current_user.id)
    await _audit(db, current_user, "ADD_SOCIETY_RESIDENT", "society_enrollments", enrollment["id"], {"society_id": society_id})
    if payload.collect_cash:
        fresh = await service.get_enrollment(enrollment["id"])
        result = await service.activate(fresh, method="cash", actor_id=current_user.id, expected_revision=int(fresh.get("revision") or 1))
        await _audit(db, current_user, "ACTIVATE_SOCIETY_ENROLLMENT", "society_enrollments", enrollment["id"], {"method": "cash"})
        return success(result["enrollment"], "Resident added — plan active")
    return success(enrollment, "Resident added — mark it paid once you collect")


@society_router.get("/{society_id}/attendance", dependencies=[Depends(require_manager_or_admin)])
async def society_attendance(
    society_id: str, month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = SocietyService(db)
    society = await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    return success(await service.attendance_month(society, month))


@society_router.post("/{society_id}/premium-bookings", dependencies=[Depends(require_manager_or_admin)])
async def staff_premium_booking(society_id: str, payload: PremiumBookingRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    await service.society_for_actor(society_id, current_user.role, current_user.service_center_id)
    result = await service.book_premium(
        payload, actor_id=current_user.id, actor_role=current_user.role, actor_center_id=current_user.service_center_id, society_id=society_id,
    )
    for b in result["bookings"]:
        await _audit(db, current_user, "SOCIETY_PREMIUM_BOOKING", "bookings", b["id"], {"society_id": society_id})
    return success(result, "Premium wash booked")


# ---------------------------------------------------------------------------
# Enrollment actions (manager / admin)
# ---------------------------------------------------------------------------


@enrollment_router.post("/{enrollment_id}/activate")
async def activate_enrollment(enrollment_id: str, payload: ActivateEnrollmentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    enrollment = await service.enrollment_for_actor(enrollment_id, current_user.role, current_user.service_center_id)
    if payload.expected_revision is None:
        raise BadRequestException("Reload the request and try again.")
    result = await service.activate(
        enrollment, method=payload.method, actor_id=current_user.id, note=payload.note, expected_revision=payload.expected_revision,
        coupon_code=payload.coupon_code, remove_coupon=payload.remove_coupon,
    )
    # Paid in cash: the WhatsApp link (if one was sent) must not take money too.
    await PaymentService(db).void_society_links(enrollment_id, "paid in cash", renewal=False)
    payment = result["enrollment"].get("payment") or {}
    await _audit(db, current_user, "ACTIVATE_SOCIETY_ENROLLMENT", "society_enrollments", enrollment_id,
                 {"method": payload.method, "amount": payment.get("amount"), "coupon_code": payment.get("coupon_code"), "discount": payment.get("discount")})
    message = "Plan active" if not result["skipped"] else f"Active — skipped {', '.join(result['skipped'])} (already on another plan)"
    return success(result["enrollment"], message)


@enrollment_router.post("/{enrollment_id}/renew")
async def renew_enrollment(enrollment_id: str, payload: ActivateEnrollmentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    enrollment = await service.enrollment_for_actor(enrollment_id, current_user.role, current_user.service_center_id)
    result = await service.renew(enrollment, method=payload.method, actor_id=current_user.id, coupon_code=payload.coupon_code)
    await PaymentService(db).void_society_links(enrollment_id, "renewed in cash", renewal=True)
    await _audit(db, current_user, "RENEW_SOCIETY_ENROLLMENT", "society_enrollments", enrollment_id,
                 {"method": payload.method, "amount": result["amount"], "coupon_code": payload.coupon_code, "discount": result.get("discount")})
    return success(result["enrollment"], f"Renewed {result['renewed']} car{'s' if result['renewed'] != 1 else ''}")


@enrollment_router.post("/{enrollment_id}/coupon-preview")
async def enrollment_coupon_preview(enrollment_id: str, payload: CouponPreviewRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """'Mark paid' / 'Renew' price summary with a coupon (nothing saved)."""
    service = SocietyService(db)
    enrollment = await service.enrollment_for_actor(enrollment_id, current_user.role, current_user.service_center_id)
    return success(await service.enrollment_coupon_preview(enrollment, payload.coupon_code, payload.renewal))


@enrollment_router.post("/{enrollment_id}/cancel")
async def cancel_enrollment(enrollment_id: str, payload: CancelEnrollmentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    enrollment = await service.enrollment_for_actor(enrollment_id, current_user.role, current_user.service_center_id)
    result = await service.cancel(enrollment, vehicle_ids=payload.vehicle_ids, actor_id=current_user.id, reason=payload.reason)
    await PaymentService(db).void_society_links(enrollment_id, "plan cancelled or changed")
    await _audit(db, current_user, "CANCEL_SOCIETY_ENROLLMENT", "society_enrollments", enrollment_id, payload.model_dump())
    return success(result, "Cancelled")


@enrollment_router.post("/{enrollment_id}/payment-link")
async def society_payment_link(enrollment_id: str, payload: SocietyPaymentLinkRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Send the resident a Razorpay link on WhatsApp; paying it activates
    (or renews) the plan by itself. Cash stays available — marking it paid
    in cash cancels the link."""
    result = await PaymentService(db).create_society_link(
        enrollment_id, renewal=payload.renewal, actor_id=current_user.id, actor_role=current_user.role,
        actor_center_id=current_user.service_center_id, send_whatsapp=payload.send_whatsapp,
    )
    await _audit(db, current_user, "SOCIETY_PAYMENT_LINK", "society_enrollments", enrollment_id,
                 {"renewal": payload.renewal, "amount": result["amount"], "reused": result["reused"], "sent": result["sent"]})
    return success(result, "Payment link sent on WhatsApp" if result["sent"] else "Payment link ready")


# ---------------------------------------------------------------------------
# Admin: plans + rate card
# ---------------------------------------------------------------------------


@plan_router.get("")
async def list_society_plans(db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await SocietyService(db).list_plans())


@plan_router.get("/rate-card")
async def get_rate_card(db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    card = await service.rate_card_view()
    card["vehicle_types"] = await service._car_types(card["premium_service_ids"])
    return success(card)


@plan_router.put("/rate-card")
async def save_rate_card(payload: RateCardRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = SocietyService(db)
    card = await service.save_rate_card(payload, current_user.id)
    card["vehicle_types"] = await service._car_types(card["premium_service_ids"])
    await _audit(db, current_user, "UPDATE_SOCIETY_RATE_CARD", "society_settings", "rate_card")
    return success(card, "Rate card saved")


@plan_router.post("")
async def create_society_plan(payload: SocietyPlanRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyService(db).create_plan(payload, current_user.id)
    await _audit(db, current_user, "CREATE_SOCIETY_PLAN", "subscription_plans", result["id"])
    return success(result, "Plan created")


@plan_router.put("/{plan_id}")
async def update_society_plan(plan_id: str, payload: SocietyPlanUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await SocietyService(db).update_plan(plan_id, payload)
    await _audit(db, current_user, "UPDATE_SOCIETY_PLAN", "subscription_plans", plan_id)
    return success(result, "Plan saved")
