"""FINAL-POLISH (2026-10-08): every `manager_paybacks[]` row on a booking
carries `reason_label` ("Service Delayed") — stamped when the payback is
written, and filled in on read for rows stored before the label existed.
Local replica-set Mongo only."""
import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.customer_wallet_service import REASON_LABELS
from tests import test_feat_booking_helpers as fb
from tests.test_feat_money2_payback import _done, _payback

pytestmark = pytest.mark.asyncio


async def test_payback_rows_on_the_booking_carry_the_reason_label(db):
    s = await fb.rig(db)
    doc, _ = await _done(db, s, delay_minutes=40)
    bid = str(doc["_id"])
    out = await _payback(db, s, s["cu"]["id"], bid, reason="delayed", method="cash", reference=None, amount=10)
    assert out["reason_label"] == "Service Delayed"
    stored = (await fb.doc(db, bid))["manager_paybacks"]
    assert stored[0]["reason"] == "delayed" and stored[0]["reason_label"] == "Service Delayed"
    view = await BookingService(db).get_booking(bid)
    assert view["manager_paybacks"][0]["reason_label"] == "Service Delayed"


async def test_old_payback_rows_get_their_label_on_read(db):
    s = await fb.rig(db)
    doc, _ = await _done(db, s, delay_minutes=40)
    bid = str(doc["_id"])
    # A row as MONEY-2 stored it, before reason_label existed.
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"paid_back_total": 15, "manager_paybacks": [
        {"id": str(ObjectId()), "amount": 10, "wallet_amount": 0, "goodwill_amount": 10, "method": "cash", "reason": "service_issue"},
        {"id": str(ObjectId()), "amount": 5, "wallet_amount": 0, "goodwill_amount": 5, "method": "cash", "reason": "mystery"},
    ]}})
    rows = (await BookingService(db).get_booking(bid))["manager_paybacks"]
    assert rows[0]["reason_label"] == REASON_LABELS["service_issue"] == "Service Issue"
    assert rows[1]["reason_label"] is None   # an unknown code stays unlabelled (the UI falls back)
    # Nothing is written on read.
    assert "reason_label" not in (await fb.doc(db, bid))["manager_paybacks"][0]
