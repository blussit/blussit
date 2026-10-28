"""RECONCILE 2026-10-28 — sweeps and views use the USABLE end of a pass
(period end, or its granted extension when later)."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.services.subscription_service import (
    find_passes_due_wash_reminder,
    find_subscriptions_ended,
    find_subscriptions_expiring_soon,
)
from tests import test_feat_planfix_helpers as pf
from tests.test_feat_planfix_attack import _rig
from tests.test_feat_planfix_helpers import set_dates

pytestmark = pytest.mark.asyncio


def _ids(rows):
    return {str(r["_id"]) for r in rows}


async def test_extended_pass_is_not_ending_today_nor_ended(db):
    s, sv, hatch, sub, customer = await _rig(db)
    await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$set": {"expiry_reminder_sent": False}})
    # Period ends in 10 hours; no extension: "ends soon" fires.
    await set_dates(db, sub, end=timedelta(hours=10))
    assert sub in _ids(await find_subscriptions_expiring_soon(db, days=2))
    # Extended 10 days past: no "ends today" notice, not "ended".
    await set_dates(db, sub, end=timedelta(hours=10), extended=timedelta(days=10))
    assert sub not in _ids(await find_subscriptions_expiring_soon(db, days=2))
    await set_dates(db, sub, end=timedelta(hours=-5), extended=timedelta(days=10))
    assert sub not in _ids(await find_subscriptions_expiring_soon(db, days=2))
    assert sub not in _ids(await find_subscriptions_ended(db))
    # Extension ending within 2 days: the heads-up fires for the Last Booking Day.
    await set_dates(db, sub, end=timedelta(hours=-5), extended=timedelta(hours=30))
    assert sub in _ids(await find_subscriptions_expiring_soon(db, days=2))


async def test_wash_reminder_uses_the_usable_end(db):
    s, sv, hatch, sub, customer = await _rig(db)
    await set_dates(db, sub, end=timedelta(hours=-5), extended=timedelta(days=8), start=timedelta(days=-40))
    await db.user_subscriptions.update_one({"_id": ObjectId(sub)}, {"$unset": {"last_used_at": "", "last_renewed_at": ""}})
    assert sub in _ids(await find_passes_due_wash_reminder(db, limit=1000))
    # Extension about to end (inside the heads-up window): wash reminder steps aside.
    await set_dates(db, sub, end=timedelta(hours=-5), extended=timedelta(hours=20))
    assert sub not in _ids(await find_passes_due_wash_reminder(db, limit=1000))
