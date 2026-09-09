"""
Regression tests for the 2026-09-09 security remediation pass
(SYSTEM_GAPS_REPORT.md §A/§B/§C): each test pins a specific exploit that
used to work.
"""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from app.schemas.subscription_schema import SubscribeRequest
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.subscription_service import UserSubscriptionService
from app.services.wallet_service import WalletService

from tests.factories import get_hatchback_type_id, make_captain, make_customer, make_service_center, make_subscription_plan


@pytest.mark.asyncio
async def test_subscription_cannot_be_spent_by_another_customer(db, cleanup):
    """A1: booking with someone else's subscription_id must 404, not waive."""
    victim = await make_customer(db)
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(victim), ObjectId(attacker)]}}))
    cleanup.append(("user_subscriptions", {"customer_id": victim}))
    plan_id = await make_subscription_plan(db, vehicle_types=[])
    cleanup.append(("subscription_plans", {"_id": ObjectId(plan_id)}))

    svc = UserSubscriptionService(db)
    sub = await svc.subscribe(victim, SubscribeRequest(plan_id=plan_id))

    with pytest.raises(NotFoundException):
        await svc.plan_consumption(sub["id"], "any-vehicle", [{"_id": "x", "category_id": None}], attacker)
    # The owner still can.
    consumption = await svc.plan_consumption(sub["id"], "any-vehicle", [{"_id": "x", "category_id": None}], victim)
    assert consumption == {"flat_count": 1}


@pytest.mark.asyncio
async def test_notification_mark_read_is_owner_scoped(db, cleanup):
    """A5: marking (and reading back) someone else's notification must 404."""
    owner = await make_customer(db)
    attacker = await make_customer(db)
    cleanup.append(("users", {"_id": {"$in": [ObjectId(owner), ObjectId(attacker)]}}))
    cleanup.append(("notifications", {"user_id": owner}))

    svc = NotificationService(db)
    await svc.notify(owner, "Private", "Sensitive booking detail")
    note = await db.notifications.find_one({"user_id": owner})
    note_id = str(note["_id"])

    with pytest.raises(NotFoundException):
        await svc.mark_read(attacker, note_id)
    fresh = await db.notifications.find_one({"_id": note["_id"]})
    assert fresh["is_read"] is False  # untouched by the failed attempt

    marked = await svc.mark_read(owner, note_id)
    assert marked["is_read"] is True


@pytest.mark.asyncio
async def test_otp_is_single_use(db, cleanup):
    """B1: a successfully verified OTP is consumed — replay fails."""
    customer = await make_customer(db)
    cleanup.append(("users", {"_id": ObjectId(customer)}))
    doc = await db.users.find_one({"_id": ObjectId(customer)})
    phone = doc["phone"]
    cleanup.append(("otp_requests", {"identifier": phone}))

    svc = AuthService(db)
    await svc.request_otp(phone)
    record = await db.otp_requests.find_one({"identifier": phone})
    otp = record["otp"]

    assert await svc.verify_otp(phone, otp) is True
    assert await svc.verify_otp(phone, otp) is False  # consumed


@pytest.mark.asyncio
async def test_withdrawal_single_pending_and_atomic_review(db, cleanup):
    """C5 + B9: one pending request at a time; a second review of the same
    request is refused (no double debit)."""
    center_id = await make_service_center(db)
    cleanup.append(("service_centers", {"_id": ObjectId(center_id)}))
    captain_id = await make_captain(db, center_id, wallet_balance=500.0)
    cleanup.append(("users", {"_id": ObjectId(captain_id)}))
    cleanup.append(("captain_wallets", {"captain_id": captain_id}))
    cleanup.append(("wallet_transactions", {"captain_id": captain_id}))
    cleanup.append(("withdrawal_requests", {"captain_id": captain_id}))

    svc = WalletService(db)
    req = await svc.request_withdrawal(captain_id, 200.0)
    with pytest.raises(BadRequestException, match="already have a withdrawal"):
        await svc.request_withdrawal(captain_id, 100.0)

    await svc.review_withdrawal(req["id"], "approved", "admin", None)
    with pytest.raises(BadRequestException, match="already been reviewed"):
        await svc.review_withdrawal(req["id"], "approved", "admin", None)

    wallet = await db.captain_wallets.find_one({"captain_id": captain_id})
    assert wallet["balance"] == 300.0  # debited exactly once
