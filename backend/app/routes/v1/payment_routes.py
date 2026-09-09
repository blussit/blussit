from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.payment_controller import PaymentController
from typing import Optional

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin, require_captain, require_customer, require_manager_or_admin
from app.core.exceptions import AppException
from app.schemas.payment_schema import CollectPaymentRequest, CreateOrderRequest, VerifyPaymentRequest
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])

_RESULT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blussit — Payment</title></head>
<body style="margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;background:#fff;font-family:system-ui,sans-serif">
<div style="text-align:center;padding:32px;max-width:360px">
<div style="font-size:44px">{icon}</div>
<h2 style="margin:12px 0 6px;color:#0a0a0a">{title}</h2>
<p style="margin:0;color:#666;font-size:14px">{detail}</p>
<p style="margin-top:18px;color:#999;font-size:12px">You can close this tab and return to WhatsApp.</p>
</div></body></html>"""


@router.post("/create-order", dependencies=[Depends(require_customer)])
async def create_order(payload: CreateOrderRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Mints a Razorpay order for the caller's own booking or a
    subscription-plan purchase — amount resolved server-side, response
    carries the public key_id the checkout modal needs."""
    return await PaymentController(db).create_order(current_user, payload)


@router.get("/collections/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def center_collections(
    service_center_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Per-captain cash/online/uncollected totals for one center — the
    manager's acknowledgment ledger of what each captain handled."""
    return await PaymentController(db).center_collections(current_user, service_center_id, date_from, date_to)


@router.get("/collections/admin", dependencies=[Depends(require_admin)])
async def admin_collections(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Platform roll-up: per-center totals, subscription revenue, and the
    needs-attention payment queue."""
    return await PaymentController(db).admin_collections(date_from, date_to)


@router.post("/collect/cash", dependencies=[Depends(require_captain)])
async def collect_cash(payload: CollectPaymentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Captain records cash received at the door for their own COMPLETED,
    still-unpaid booking (founder spec: the 'payment accepted' tap)."""
    return await PaymentController(db).collect_cash(current_user, payload)


@router.post("/collect/link", dependencies=[Depends(require_captain)])
async def collect_link(payload: CollectPaymentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The doorstep QR's target: a Razorpay payment link for the captain's
    own unpaid booking (an existing pending link is reused)."""
    return await PaymentController(db).collect_link(current_user, payload)


@router.get("/collect/status/{booking_id}", dependencies=[Depends(require_captain)])
async def collect_status(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Polled while the QR is on screen — actively syncs this booking's
    links against Razorpay and reports the live payment state."""
    return await PaymentController(db).collect_status(current_user, booking_id)


@router.get("/link-callback", include_in_schema=False)
async def payment_link_callback(request: Request, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Where Razorpay redirects the customer's browser after paying a
    payment link (WhatsApp bookings). Unauthenticated by necessity — the
    payer has no app session — so the SIGNATURE in the query params is
    the entire trust decision; a bad one applies nothing. The reminder
    loop's link sweep is the backstop when this redirect never happens."""
    try:
        result = await PaymentService(db).verify_link_callback(dict(request.query_params))
        return HTMLResponse(_RESULT_PAGE.format(
            icon="✅", title="Payment received!",
            detail=f"Booking {result.get('booking_number') or ''} is paid. We've noted it — see you at your doorstep!",
        ))
    except AppException as exc:
        return HTMLResponse(_RESULT_PAGE.format(
            icon="⚠️", title="Payment not confirmed",
            detail=exc.message + " If money left your account, it will reflect shortly or auto-refund.",
        ), status_code=400)


@router.post("/verify", dependencies=[Depends(require_customer)])
async def verify_payment(payload: VerifyPaymentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Signature check (HMAC-SHA256 over order_id|payment_id with the key
    secret) — only a match applies the purchase; a mismatch is a 400 and
    nothing is marked paid."""
    return await PaymentController(db).verify(current_user, payload)
