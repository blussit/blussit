"""SMALL-5 (2026-10-07): a manager payback (MONEY-2) is mirrored on the
booking as `manager_paybacks[]` + `paid_back_total`. That is money between
the center and the customer — a captain's views never carry it (his job
list, the booking read, the visit read, every action answer, including the
priority flag he may set on his own job). The customer's own view keeps it
(it's their money); staff see it."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio

_PAYBACK_FIELDS = ("manager_paybacks", "paid_back_total")


async def test_captain_views_never_carry_the_paybacks_customer_and_staff_do(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.assign(db, b["id"], s)
    # What CustomerWalletService.payback leaves on the booking.
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {
        "paid_back_total": 40.0,
        "manager_paybacks": [{"amount": 40.0, "wallet_amount": 0.0, "goodwill_amount": 40.0, "method": "cash",
                              "reason": "delayed", "by": s["mgr"]["id"], "at": datetime.now(timezone.utc)}],
    }})
    async with h.client() as c:
        cap_one = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["cap"]["h"])).json()["data"]
        cap_jobs = (await c.get("/api/v1/bookings/my-jobs", headers=s["cap"]["h"])).json()["data"]
        r = await c.patch(f"/api/v1/bookings/{b['id']}/priority", json={"priority": "high"}, headers=s["cap"]["h"])
        assert r.status_code == 200, r.text
        cap_priority = r.json()["data"]
        group_id = cap_one.get("booking_group_id")
        cap_group = (await c.get(f"/api/v1/bookings/group/{group_id}", headers=s["cap"]["h"])).json()["data"] if group_id else []
        mine = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["cu"]["h"])).json()["data"]
        staff = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["mgr"]["h"])).json()["data"]
    job = next(x for x in cap_jobs if x["id"] == b["id"])
    group_rows = [x for x in (cap_group if isinstance(cap_group, list) else cap_group.get("bookings") or []) if x.get("id") == b["id"]]
    for view in (cap_one, job, cap_priority, *group_rows):
        for field in _PAYBACK_FIELDS:
            assert field not in view, field
        assert "platform_earning" not in view
    # The priority answer is a captain read like the others (no service code).
    assert "service_code" not in cap_priority
    # The customer's own money: visible to them, and to staff.
    assert mine["paid_back_total"] == 40.0 and len(mine["manager_paybacks"]) == 1
    assert staff["paid_back_total"] == 40.0 and len(staff["manager_paybacks"]) == 1
