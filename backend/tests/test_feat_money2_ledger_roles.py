"""MONEY-2 (founder 2026-10-07): wallet history by role. The customer and
an admin see every entry in full. A manager sees the customer's whole
ledger (amount, kind, date, balance after) but an entry tied to ANOTHER
center's booking shows "Other Center" instead of its booking details,
note or reason. Staff responses mark each entry `own_center`."""
import pytest
from bson import ObjectId

from app.services.customer_wallet_service import CustomerWalletService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def _two_center_ledger(db):
    a = await fb.rig(db)
    b = await fb.rig(db)
    cid = a["cu"]["id"]
    w = CustomerWalletService(db)
    mine = await fb.book(db, a["cu"], a["when"], a["keys"][0])
    theirs = await fb.book(db, a["cu"], a["when"], a["keys"][1])
    # The second booking belongs to center B (the customer booked there too).
    await db.bookings.update_one({"_id": ObjectId(theirs["id"])}, {"$set": {"service_center_id": b["center_id"]}})
    await w.post(cid, 120, "price_reduced", key=f"m2l:{cid}:a", booking_id=mine["id"], note="A-side note",
                 actor_id=a["mgr"]["id"], actor_role="manager", actor_name="Manager A")
    await w.post(cid, 80, "overpayment", key=f"m2l:{cid}:b", booking_id=theirs["id"], note="B-side secret reason",
                 actor_id=b["mgr"]["id"], actor_role="manager", actor_name="Manager B",
                 meta={"method": "upi", "reference": "UTR-B-SECRET", "reason": "complaint"})
    await w.post(cid, -30, "adjustment", key=f"m2l:{cid}:c", note="Admin fix", actor_role="admin")
    return a, b, cid, mine, theirs


def _by_number(items: list[dict], number: str | None) -> dict:
    return next(i for i in items if i.get("booking_number") == number)


async def test_customer_sees_the_whole_ledger(db):
    a, _b, cid, mine, theirs = await _two_center_ledger(db)
    async with h.client() as c:
        r = await c.get("/api/v1/wallet/me", headers=a["cu"]["h"])
    data = r.json()["data"]
    assert r.status_code == 200 and data["balance"] == 170 and len(data["items"]) == 3
    numbers = {i["booking_number"] for i in data["items"]}
    assert {mine["booking_number"], theirs["booking_number"]} <= numbers
    assert any(i["note"] == "B-side secret reason" for i in data["items"])


async def test_admin_sees_everything(db):
    a, _b, cid, mine, theirs = await _two_center_ledger(db)
    admin = await h.admin(db)
    async with h.client() as c:
        r = await c.get(f"/api/v1/customers/{cid}/wallet", headers=admin["h"])
    items = r.json()["data"]["items"]
    assert r.status_code == 200 and len(items) == 3
    assert all(i["own_center"] is True for i in items)
    other = _by_number(items, theirs["booking_number"])
    assert other["note"] == "B-side secret reason" and other["booking_id"] == theirs["id"] and other["reference"] == "UTR-B-SECRET"


async def test_manager_sees_amounts_but_not_other_centers_booking_details(db):
    a, b, cid, mine, theirs = await _two_center_ledger(db)
    async with h.client() as c:
        ra = await c.get(f"/api/v1/customers/{cid}/wallet", headers=a["mgr"]["h"])
        rb = await c.get(f"/api/v1/customers/{cid}/wallet", headers=b["mgr"]["h"])
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text, rb.text)
    a_items = ra.json()["data"]["items"]
    assert len(a_items) == 3 and ra.json()["data"]["balance"] == 170
    own = _by_number(a_items, mine["booking_number"])
    assert own["own_center"] is True and own["note"] == "A-side note" and own["booking_id"] == mine["id"]
    hidden = next(i for i in a_items if i["amount"] == 80)
    assert hidden["own_center"] is False
    assert hidden["booking_label"] == "Other Center"
    for field in ("booking_id", "booking_number", "note", "reference", "reason", "actor_name"):
        assert hidden.get(field) is None, field
    assert hidden["kind"] == "overpayment" and hidden["balance_after"] == 200 and hidden["created_at"]
    plain = next(i for i in a_items if i["amount"] == -30)
    assert plain["own_center"] is True and plain["note"] == "Admin fix"
    # Center B's manager: the mirror image.
    b_items = rb.json()["data"]["items"]
    assert _by_number(b_items, theirs["booking_number"])["own_center"] is True
    mirrored = next(i for i in b_items if i["amount"] == 120)
    assert mirrored["own_center"] is False and mirrored["booking_number"] is None and mirrored["note"] is None
