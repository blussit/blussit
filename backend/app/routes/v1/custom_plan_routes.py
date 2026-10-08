"""Custom multi-car plans (manager cart) — /api/v1/subscriptions/custom-plans.

Mounted inside the subscriptions router (subscription_routes.py), so no
separate registration in main.py is needed. Staff endpoints: a manager acts
on their own center's carts only, an admin on any. See CustomPlanService."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import (
    CurrentUser,
    PaginationParams,
    get_current_user,
    get_db,
    require_customer,
    require_manager_or_admin,
)
from app.core.responses import paginated, success
from app.schemas.custom_plan_schema import (
    CustomPlanCancelRequest,
    CustomPlanCashRequest,
    CustomPlanCreateRequest,
    CustomPlanLinkRequest,
    CustomPlanPreviewRequest,
    CustomPlanRefundRequest,
    CustomPlanRenewRequest,
    CustomPlanReviseRequest,
)
from app.services.audit_service import AuditService
from app.services.custom_plan_service import CustomPlanService

router = APIRouter(prefix="/custom-plans", tags=["Custom Plans"])


async def _audit(db, user: CurrentUser, action: str, cart_id: str, details: dict | None = None) -> None:
    await AuditService(db).log_action(user.id, user.role, action, "custom_plans", cart_id, details)


def _actor(user: CurrentUser) -> dict:
    return {"actor_id": user.id, "actor_role": user.role, "actor_center_id": user.service_center_id}


@router.post("/preview", dependencies=[Depends(require_manager_or_admin)])
async def preview_custom_plan(payload: CustomPlanPreviewRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Read-only price of a cart — Σ count × each car type's standard
    service price, less the (capped) discount. Exactly what create/link/cash
    will charge; nothing is saved."""
    return success(await CustomPlanService(db).preview(payload, actor_role=current_user.role, actor_center_id=current_user.service_center_id))


@router.post("", dependencies=[Depends(require_manager_or_admin)])
async def create_custom_plan(payload: CustomPlanCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await CustomPlanService(db).create(payload, **_actor(current_user))
    await _audit(db, current_user, "CREATE_CUSTOM_PLAN", result["id"], {"customer_id": result["customer_id"], "total_amount": result["total_amount"],
                                                                      "discount_amount": result["discount_amount"], "cars": result["car_count"]})
    return success(result, "Custom plan saved")


@router.get("", dependencies=[Depends(require_manager_or_admin)])
async def list_custom_plans(
    status: Optional[str] = Query(None, pattern=r"^(draft|awaiting_payment|activating|active|needs_review|refunded|cancelled)$"),
    customer_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    service_center_id: Optional[str] = Query(None, pattern=r"^[0-9a-fA-F]{24}$"),
    pagination: PaginationParams = Depends(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """A manager: own center's carts (the center param is ignored); admin: any."""
    items, total = await CustomPlanService(db).list_for_staff(
        actor_role=current_user.role, actor_center_id=current_user.service_center_id, service_center_id=service_center_id,
        status=status, customer_id=customer_id, page=pagination.page, page_size=pagination.page_size,
    )
    return paginated(items, pagination.page, pagination.page_size, total)


@router.get("/my", dependencies=[Depends(require_customer)])
async def my_custom_plans(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The customer's custom plans, each with its cars, their passes and
    per-service washes left (plus the payment link while unpaid)."""
    return success(await CustomPlanService(db).list_mine(current_user.id))


@router.get("/{cart_id}", dependencies=[Depends(require_manager_or_admin)])
async def get_custom_plan(cart_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    service = CustomPlanService(db)
    return success(await service.view(await service.for_actor(cart_id, current_user.role, current_user.service_center_id)))


@router.put("/{cart_id}", dependencies=[Depends(require_manager_or_admin)])
async def revise_custom_plan(cart_id: str, payload: CustomPlanReviseRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Replace the cart (before payment), guarded on `expected_revision` —
    409 if it changed meanwhile. Any link already sent is voided."""
    result = await CustomPlanService(db).revise(cart_id, payload, **_actor(current_user))
    await _audit(db, current_user, "REVISE_CUSTOM_PLAN", cart_id, {"revision": result["revision"], "total_amount": result["total_amount"],
                                                                   "discount_amount": result["discount_amount"]})
    return success(result, "Custom plan updated")


@router.post("/{cart_id}/link", dependencies=[Depends(require_manager_or_admin)])
async def send_custom_plan_link(cart_id: str, payload: CustomPlanLinkRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """One Razorpay link for the whole cart at this revision (re-sent, not
    duplicated). Paying it activates every car's pass by itself."""
    result = await CustomPlanService(db).send_link(
        cart_id, expected_revision=payload.expected_revision, send_whatsapp=payload.send_whatsapp, **_actor(current_user),
    )
    await _audit(db, current_user, "CUSTOM_PLAN_LINK", cart_id, {"order_id": result["order_id"], "amount": result["amount"], "reused": result["reused"]})
    return success(result, "Payment link sent" if payload.send_whatsapp else "Payment link ready")


@router.post("/{cart_id}/cash", dependencies=[Depends(require_manager_or_admin)])
async def mark_custom_plan_cash(cart_id: str, payload: CustomPlanCashRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Paid in cash: every car's pass starts today. Refused while a car
    already holds another plan; any link still out is voided."""
    result = await CustomPlanService(db).mark_cash_paid(cart_id, expected_revision=payload.expected_revision, note=payload.note, **_actor(current_user))
    view = result["custom_plan"]
    await _audit(db, current_user, "CUSTOM_PLAN_CASH", cart_id, {"amount": view["total_amount"], "activated": result["activated"], "skipped": result["skipped"]})
    message = "Plan active" if not result["skipped"] else f"Active — skipped {', '.join(p or 'a car' for p in result['skipped'])} (already on another plan)"
    return success(view, message)


@router.post("/{cart_id}/cancel", dependencies=[Depends(require_manager_or_admin)])
async def cancel_custom_plan(cart_id: str, payload: CustomPlanCancelRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Before payment only (a paid plan's passes are handled one by one)."""
    result = await CustomPlanService(db).cancel(cart_id, reason=payload.reason, **_actor(current_user))
    await _audit(db, current_user, "CANCEL_CUSTOM_PLAN", cart_id, {"reason": payload.reason})
    return success(result, "Custom plan cancelled")


@router.post("/{cart_id}/renew", dependencies=[Depends(require_manager_or_admin)])
async def renew_custom_plan(cart_id: str, payload: CustomPlanRenewRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """A NEW cart renewing this paid one (`renewal_of`): its cars and counts
    (or the edited `cars`) at today's prices, draft until paid by link or
    cash. On activation a car whose old pass is still live starts the day
    after that pass's Last Booking Day; others start today. Customers
    can't renew here — they pay the renewal link."""
    result = await CustomPlanService(db).renew(cart_id, payload, **_actor(current_user))
    await _audit(db, current_user, "RENEW_CUSTOM_PLAN", result["id"], {"renewal_of": cart_id, "total_amount": result["total_amount"],
                                                                       "discount_amount": result["discount_amount"], "cars": result["car_count"]})
    return success(result, "Renewal saved — send the link or take cash")


@router.post("/{cart_id}/cars/{car_ref}/refund", dependencies=[Depends(require_manager_or_admin)])
async def refund_custom_plan_car(
    cart_id: str, car_ref: str, payload: CustomPlanRefundRequest,
    current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Refund ONE car (`car_ref`: its 0-based index in `cars`, or its
    vehicle_id) to the customer's wallet: at most its `refundable_amount`
    (unused washes at the per-wash price paid; a skipped car: its whole
    share). Cancels that car's pass. Idempotent — a repeat returns
    `already: true` and credits nothing."""
    result = await CustomPlanService(db).refund_car(cart_id, car_ref, amount=payload.amount, reason=payload.reason, **_actor(current_user))
    if not result["already"]:
        refund = result["refund"] or {}
        await _audit(db, current_user, "REFUND_CUSTOM_PLAN_CAR", cart_id, {
            "vehicle_id": result["vehicle_id"], "registration_number": result["registration_number"], "amount": refund.get("amount"),
            "max_amount": refund.get("max_amount"), "washes": refund.get("washes"), "reason": payload.reason,
        })
    amount = (result["refund"] or {}).get("amount")
    message = "Already refunded" if result["already"] else f"₹{amount} added to the customer's wallet"
    return success(result, message)
