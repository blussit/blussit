import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.payment_controller import PaymentController
from typing import Optional

from app.core.config import settings
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin, require_captain, require_customer, require_manager_or_admin
from app.core.exceptions import AppException
from app.schemas.payment_schema import (
    CollectPaymentRequest,
    CreateOrderRequest,
    PaymentFailureReport,
    ResolveAttentionRequest,
    VerifyPaymentRequest,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])

_RESULT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blussit — Payment</title>{pixel}</head>
<body style="margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;background:#fff;font-family:system-ui,sans-serif">
<div style="text-align:center;padding:32px;max-width:360px">
<div style="font-size:44px">{icon}</div>
<h2 style="margin:12px 0 6px;color:#0a0a0a">{title}</h2>
<p style="margin:0;color:#666;font-size:14px">{detail}</p>
<p style="margin-top:18px;color:#999;font-size:12px">You can close this tab and return to WhatsApp.</p>
</div></body></html>"""

# Meta's standard pixel snippet + the Purchase, for a website booking whose
# payment link just confirmed it (PaymentService.link_purchase_event).
_PIXEL = """<script>
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,
document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init', %(pixel_id)s);
fbq('track', 'PageView');
fbq('track', 'Purchase', {value: %(value)s, currency: 'INR', content_category: 'booking'}, {eventID: %(event_id)s});
</script>"""


async def _purchase_pixel(db: AsyncIOMotorDatabase, link_id: str | None) -> str:
    if not settings.META_PIXEL_ID or not link_id:
        return ""
    try:
        event = await PaymentService(db).link_purchase_event(link_id)
    except Exception:  # noqa: BLE001 — tracking must never cost the customer their receipt page
        return ""
    if not event:
        return ""
    return _PIXEL % {
        "pixel_id": json.dumps(settings.META_PIXEL_ID),
        "value": json.dumps(event["value"]),
        "event_id": json.dumps(event["event_id"]),
    }


@router.post("/create-order", dependencies=[Depends(require_customer)])
async def create_order(payload: CreateOrderRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Mints a Razorpay order for the caller's own booking or a
    subscription-plan purchase — amount resolved server-side, response
    carries the public key_id the checkout modal needs."""
    return await PaymentController(db).create_order(current_user, payload)


@router.get("/collections/center/{service_center_id}", dependencies=[Depends(require_manager_or_admin)])
async def center_collections(
    service_center_id: str,
    # A malformed date used to reach strptime and 500.
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
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
        if result.get("status") == "needs_attention":
            return HTMLResponse(_RESULT_PAGE.format(
                pixel="", icon="🕒", title="Payment received — under review",
                detail="We got your payment but couldn't apply it automatically. Our team will fix or refund it — no need to pay again.",
            ))
        return HTMLResponse(_RESULT_PAGE.format(
            pixel=await _purchase_pixel(db, request.query_params.get("razorpay_payment_link_id")),
            icon="✅", title="Payment received!",
            detail=f"Booking {result.get('booking_number') or ''} is paid. We've noted it — see you at your doorstep!",
        ))
    except AppException as exc:
        return HTMLResponse(_RESULT_PAGE.format(
            pixel="", icon="⚠️", title="Payment not confirmed",
            detail=exc.message + " If money left your account, it will reflect shortly or auto-refund.",
        ), status_code=400)


@router.post("/verify", dependencies=[Depends(require_customer)])
async def verify_payment(payload: VerifyPaymentRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Signature check (HMAC-SHA256 over order_id|payment_id with the key
    secret) — only a match applies the purchase; a mismatch is a 400 and
    nothing is marked paid."""
    return await PaymentController(db).verify(current_user, payload)


@router.post("/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Razorpay's server-to-server events. Unauthenticated by necessity —
    the X-Razorpay-Signature HMAC over the raw body (RAZORPAY_WEBHOOK_SECRET)
    is the whole trust decision. 404 while no secret is configured; 2xx for
    every event it accepted, handled or ignored; a 5xx only when processing
    failed, so Razorpay retries it."""
    result = await PaymentService(db).handle_webhook(
        await request.body(),
        request.headers.get("X-Razorpay-Signature"),
        request.headers.get("X-Razorpay-Event-Id"),
    )
    return {"success": True, **result}


@router.post("/failure", dependencies=[Depends(require_customer)])
async def report_payment_failure(payload: PaymentFailureReport, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """The checkout's payment.failed event — records why the attempt failed
    so the booking page can say so. Changes nothing that's paid."""
    return await PaymentController(db).report_failure(current_user, payload)


@router.get("/status", dependencies=[Depends(require_customer)])
async def payment_status(
    order_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Polled while a paid checkout is being confirmed (verify didn't get
    through). Re-checks an open order against Razorpay and settles it the
    moment the money is there."""
    return await PaymentController(db).status(current_user, order_id, subscription_id)


@router.get("/bookings/{booking_id}/state", dependencies=[Depends(require_customer)])
async def booking_payment_state(booking_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Last failed attempt + any received-but-unapplied money for the
    customer's own booking (or its visit)."""
    return await PaymentController(db).booking_state(current_user, booking_id)


@router.post("/attention/{order_id}/resolve", dependencies=[Depends(require_admin)])
async def resolve_payment_attention(
    order_id: str, payload: ResolveAttentionRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Takes a handled (refunded / activated) payment off the open
    needs-attention queue, with a note of what was done."""
    return await PaymentController(db).resolve_attention(current_user, order_id, payload)
