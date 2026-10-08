"""Founder decision (10-07): a manager's discount and tip both count into the
booking total and must be visible to admin on their own — the collections
report (center and admin roll-up) shows them as separate lines, from
completed jobs only, without touching captain money."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from tests.factories import make_service_center

pytestmark = pytest.mark.asyncio


async def test_collections_show_manager_discounts_and_tips(db, cleanup):
    from app.services.payment_service import PaymentService

    center = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("bookings", {"service_center_id": center}))
    day = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    base = {"service_center_id": center, "payment_status": "paid", "payment_method": "cash", "scheduled_date": day,
            "is_deleted": False, "completed_by_role": "manager", "captain_earning": 0.0}
    await db.bookings.insert_many([
        {**base, "booking_number": f"BKC1{ObjectId()}", "status": "completed", "subtotal": 349, "manager_discount": 49, "tip_amount": 30, "total_amount": 330},
        {**base, "booking_number": f"BKC2{ObjectId()}", "status": "completed", "subtotal": 499, "total_amount": 499},
        {**base, "booking_number": f"BKC3{ObjectId()}", "status": "pending", "payment_status": "pending", "manager_discount": 100, "tip_amount": 100, "total_amount": 0},
    ])
    d = day.date().isoformat()
    svc = PaymentService(db)
    mine = await svc.center_collections(center, "admin", None, d, d)
    assert mine["totals"]["manager_discount_amount"] == 49
    assert mine["totals"]["tip_amount"] == 30
    assert mine["totals"]["cash_amount"] == 829, "discount and tip are already inside the collected amount"
    roll = await svc.admin_collections(d, d)
    row = next(r for r in roll["rows"] if r["service_center_id"] == center)
    assert row["manager_discount_amount"] == 49 and row["tip_amount"] == 30
