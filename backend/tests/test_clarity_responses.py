"""Response-clarity pass (2026-10-29): every result says WHAT happened —
which booking, how much, when — and the failures name the real problem.
Wording is asserted loosely (key facts), error_codes stay as they were."""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException, NotFoundException
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


async def test_wrong_role_403_names_the_roles(db):
    s = await fb.rig(db)
    async with h.client() as c:
        r = await c.get("/api/v1/audit-logs", headers=s["cu"]["h"])
    body = r.json()
    assert r.status_code == 403 and body["error_code"] == "FORBIDDEN"
    assert "only for an admin" in body["message"] and "signed in as a customer" in body["message"]
    assert body["details"] == {"required_roles": ["admin"], "your_role": "customer"}


async def test_cancel_and_reschedule_messages_say_what_happened(db):
    s = await fb.rig(db)
    cu = s["cu"]
    b = await fb.book(db, cu, s["when"], s["keys"][0])
    async with h.client() as c:
        moved = await c.post(
            f"/api/v1/bookings/{b['id']}/reschedule", json={"scheduled_date": s["when"], "scheduled_slot": s["keys"][1]}, headers=cu["h"],
        )
        assert moved.status_code == 200, moved.text
        msg = moved.json()["message"]
        assert b["booking_number"] in msg and "AM" in msg or "PM" in msg
        assert "A captain will be assigned shortly" in msg
        # Free cancel (> 4 h away): the booking number and "no charge".
        free = await c.post(f"/api/v1/bookings/{b['id']}/cancel", json={"reason": "Plans changed"}, headers=cu["h"])
        assert free.status_code == 200
        text = free.json()["message"]
        assert b["booking_number"] in text and "cancelled" in text and "No charge" in text
        # Late cancel: names the charge and the wallet effect.
        b2 = await fb.book(db, cu, s["when"], s["keys"][2])
        await fb.slot_in(db, b2["id"], 2 * 60)
        late = await c.post(f"/api/v1/bookings/{b2['id']}/cancel", json={"reason": "Plans changed"}, headers=cu["h"])
        text = late.json()["message"]
        assert "Late-cancellation charge: ₹50" in text and "charged to your Blussit wallet" in text
        # A manager cancelling for the customer is told whose wallet it is.
        b3 = await fb.book(db, cu, s["when"], s["keys"][0])
        await fb.slot_in(db, b3["id"], 2 * 60)
        staff = await c.post(
            f"/api/v1/bookings/{b3['id']}/cancel", json={"reason": "Customer called", "at_customer_request": True}, headers=s["mgr"]["h"],
        )
        assert "customer's Blussit wallet" in staff.json()["message"]


async def test_assign_message_names_captain_and_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/assign-captain", json={"captain_id": s["cap"]["id"]}, headers=s["mgr"]["h"])
    assert r.status_code == 200, r.text
    cap = await db.users.find_one({"_id": ObjectId(s["cap"]["id"])})
    assert cap["full_name"] in r.json()["message"] and b["booking_number"] in r.json()["message"]


async def test_recycle_bin_messages_count_the_bookings(db):
    s = await fb.rig(db)
    adm = await h.admin(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    async with h.client() as c:
        gone = await c.post(f"/api/v1/bookings/{b['id']}/delete", headers=adm["h"])
        assert gone.status_code == 200, gone.text
        assert "1 booking moved to the recycle bin" in gone.json()["message"] and "30 days" in gone.json()["message"]
        back = await c.post(f"/api/v1/bookings/{b['id']}/restore", headers=adm["h"])
        assert "1 booking restored" in back.json()["message"]


async def test_subscription_cancel_refuses_a_plan_that_is_already_over(db):
    s = await fb.rig(db)
    cu = s["cu"]
    sub_id = await fb.pass_for(db, cu["id"])
    async with h.client() as c:
        first = await c.post(f"/api/v1/subscriptions/{sub_id}/cancel", headers=cu["h"])
        assert first.status_code == 200, first.text
        assert "cancelled" in first.json()["message"] and "Auto-pay" in first.json()["message"]
        again = await c.post(f"/api/v1/subscriptions/{sub_id}/cancel", headers=cu["h"])
        assert again.status_code == 400 and "already cancelled" in again.json()["message"]
        # An ended pass is not rewritten as "cancelled".
        s2 = await fb.pass_for(db, cu["id"])
        await db.user_subscriptions.update_one({"_id": ObjectId(s2)}, {"$set": {"end_date": datetime.utcnow() - timedelta(days=2)}})
        ended = await c.post(f"/api/v1/subscriptions/{s2}/cancel", headers=cu["h"])
        assert ended.status_code == 400 and "already ended" in ended.json()["message"]
    assert (await db.user_subscriptions.find_one({"_id": ObjectId(s2)}))["status"] != "cancelled"


async def test_enquiry_status_typo_is_a_400_not_a_missing_enquiry(db):
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.post(f"/api/v1/subscriptions/enquiries/{'0' * 24}/status", params={"status": "done"}, headers=adm["h"])
    body = r.json()
    assert r.status_code == 400 and body["error_code"] == "BAD_REQUEST"
    assert "new, contacted, closed" in body["message"] and body["details"]["allowed"] == ["new", "contacted", "closed"]
    async with h.client() as c:
        missing = await c.post(f"/api/v1/subscriptions/enquiries/{'0' * 24}/status", params={"status": "closed"}, headers=adm["h"])
    assert missing.status_code == 404


async def test_coupon_minimum_order_shows_whole_rupees(db):
    from app.schemas.coupon_schema import CouponCreateRequest
    from app.services.coupon_service import CouponService

    s = await fb.rig(db)
    now = datetime.utcnow()
    svc = CouponService(db)
    code = f"MINORD{ObjectId()}"[:14].upper()
    await svc.create(CouponCreateRequest(
        code=code, coupon_type="flat", value=50, min_order_value=500, valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=5), total_usage_limit=10, usage_limit_per_user=1,
    ))
    with pytest.raises(BadRequestException) as err:
        await svc.validate_and_compute_discount(code, 100, s["cu"]["id"], use="services")
    assert "₹500 required" in err.value.message and "500.0" not in err.value.message


async def test_withdrawal_over_balance_says_how_much_is_available(db):
    from app.services.wallet_service import WalletService

    s = await fb.rig(db)
    with pytest.raises(BadRequestException) as err:
        await WalletService(db).request_withdrawal(s["cap"]["id"], 100000)
    assert "₹100000" in err.value.message and "wallet balance is ₹500" in err.value.message
    assert err.value.details == {"requested": 100000, "balance": 500.0}


async def test_wallet_adjust_and_withdrawal_review_messages(db):
    s = await fb.rig(db)
    adm = await h.admin(db)
    async with h.client() as c:
        adj = await c.post(
            f"/api/v1/wallet/captain/{s['cap']['id']}/adjust", json={"amount": 120, "description": "Fuel refund"}, headers=adm["h"],
        )
        assert adj.status_code == 200, adj.text
        assert "₹120 added to the captain's wallet" in adj.json()["message"] and "New balance: ₹620" in adj.json()["message"]
        ask = await c.post("/api/v1/wallet/withdrawals", json={"amount": 200}, headers=s["cap"]["h"])
        assert ask.status_code == 200 and "₹200 requested" in ask.json()["message"]
        wid = ask.json()["data"]["id"]
        ok = await c.put(f"/api/v1/wallet/withdrawals/{wid}/review", json={"status": "approved"}, headers=adm["h"])
        assert "₹200 approved" in ok.json()["message"] and "mark it paid" in ok.json()["message"]
        back = await c.put(f"/api/v1/wallet/withdrawals/{wid}/review", json={"status": "rejected"}, headers=adm["h"])
        assert "cancelled and returned to the captain's wallet" in back.json()["message"]


async def test_customer_wallet_adjust_message_has_amount_and_balance(db):
    s = await fb.rig(db)
    adm = await h.admin(db)
    async with h.client() as c:
        r = await c.post(
            f"/api/v1/customers/{s['cu']['id']}/wallet/adjust", json={"amount": 75, "note": "Goodwill credit"}, headers=adm["h"],
        )
    assert r.status_code == 200, r.text
    assert "₹75 added to the customer's wallet" in r.json()["message"] and "New balance: ₹75" in r.json()["message"]


async def test_manager_summary_does_not_count_cancelled_bookings_today(db):
    from app.services.analytics_service import AnalyticsService
    from app.utils.timezone import now_ist

    s = await fb.rig(db)
    today = now_ist().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    base = {"service_center_id": s["center_id"], "scheduled_date": today, "is_deleted": False, "delay_minutes": 10}
    await db.bookings.insert_many([
        {**base, "booking_number": "CLR-1", "status": "pending"},
        {**base, "booking_number": "CLR-2", "status": "cancelled"},
        {**base, "booking_number": "CLR-3", "status": "cancelled"},
    ])
    summary = await AnalyticsService(db).manager_summary(s["center_id"])
    assert summary["bookings_today"] == 1 and summary["delayed_today"] == 1


async def test_complaint_notifications_say_what_changed(db):
    from app.schemas.complaint_schema import ComplaintUpdateRequest
    from app.services.complaint_service import ComplaintService

    s = await fb.rig(db)
    now = datetime.utcnow()
    cid = (await db.complaints.insert_one({
        "customer_id": s["cu"]["id"], "service_center_id": s["center_id"], "subject": "Dull finish", "status": "open",
        "priority": "medium", "replies": [], "is_deleted": False, "created_at": now, "updated_at": now,
    })).inserted_id
    svc = ComplaintService(db)
    await svc.update(str(cid), ComplaintUpdateRequest(status="resolved", resolution_note="Re-washed free of charge"), s["mgr"]["id"], "admin")
    rows = await fb.bell(db, s["cu"]["id"], "Complaint update")
    text = rows[-1]["message"]
    assert "'Dull finish' is now Resolved" in text and "Re-washed free of charge" in text
    # Re-prioritising alone stays internal — no new message.
    before = len(rows)
    await svc.update(str(cid), ComplaintUpdateRequest(priority="high"), s["mgr"]["id"], "admin")
    assert len(await fb.bell(db, s["cu"]["id"], "Complaint update")) == before
    # A reply that moves the status says so and quotes the reply.
    await svc.add_reply(str(cid), s["mgr"]["id"], "admin", None, "We re-checked the car", "in_progress")
    text = (await fb.bell(db, s["cu"]["id"], "Complaint update"))[-1]["message"]
    assert "is now In progress" in text and "We re-checked the car" in text


async def test_document_missing_from_storage_is_a_404(db, monkeypatch):
    from app.core import storage

    monkeypatch.setattr(storage.settings, "STORAGE_PROVIDER", "local")
    with pytest.raises(NotFoundException):
        await storage.download_private_document("documents/" + "a" * 32 + ".jpg")
    with pytest.raises(NotFoundException):
        await storage.download_private_document("../etc/passwd")


async def test_inventory_adjust_messages_name_the_item(db):
    s = await fb.rig(db)
    adm = await h.admin(db)
    async with h.client() as c:
        made = await c.post("/api/v1/inventory", json={
            "service_center_id": s["center_id"], "item_name": "Shampoo", "unit": "litre", "quantity_available": 5, "reorder_level": 1,
        }, headers=adm["h"])
        assert made.status_code in (200, 201), made.text
        item = made.json()["data"]["id"]
        ok = await c.post(f"/api/v1/inventory/{item}/adjust", json={"delta": -2, "reason": "Used"}, headers=adm["h"])
        assert ok.status_code == 200, ok.text
        assert "2 taken from Shampoo" in ok.json()["message"] and "In stock now: 3" in ok.json()["message"]
        bad = await c.post(f"/api/v1/inventory/{item}/adjust", json={"delta": -9, "reason": "Used"}, headers=adm["h"])
        assert bad.status_code == 400 and "Only 3 of Shampoo in stock" in bad.json()["message"]


async def test_account_without_created_at_still_loads_me(db):
    s = await fb.rig(db)
    await db.users.update_one({"_id": ObjectId(s["cap"]["id"])}, {"$unset": {"created_at": ""}})
    async with h.client() as c:
        me = await c.get("/api/v1/auth/me", headers=s["cap"]["h"])
    assert me.status_code == 200, me.text
    assert me.json()["data"]["created_at"] is None


async def test_report_params_that_used_to_500_are_clear_400_422s(db):
    adm = await h.admin(db)
    s = await fb.rig(db)
    async with h.client() as c:
        # A date that passes the YYYY-MM-DD shape but is no real day.
        bad = await c.get(f"/api/v1/payments/collections/center/{s['center_id']}", params={"date_from": "2026-13-45"}, headers=adm["h"])
        assert bad.status_code == 400 and "YYYY-MM-DD" in bad.json()["message"]
        shape = await c.get("/api/v1/payments/collections/admin", params={"date_from": "zzz"}, headers=adm["h"])
        assert shape.status_code == 422 and shape.json()["error_code"] == "VALIDATION_ERROR"
        backwards = await c.get("/api/v1/payments/collections/admin", params={"date_from": "2026-10-20", "date_to": "2026-10-01"}, headers=adm["h"])
        assert backwards.status_code == 400 and "can't be after" in backwards.json()["message"]
        # Absurd page counts no longer overflow into a 500.
        assert (await c.get("/api/v1/whatsapp/campaigns", params={"page": "99999999999999999999"}, headers=adm["h"])).status_code == 422


async def test_validation_error_message_names_the_field(db):
    adm = await h.admin(db)
    s = await fb.rig(db)
    async with h.client() as c:
        r = await c.post(f"/api/v1/wallet/captain/{s['cap']['id']}/adjust", json={"amount": "lots", "description": "x"}, headers=adm["h"])
    body = r.json()
    assert r.status_code == 422 and body["error_code"] == "VALIDATION_ERROR"
    assert body["message"].startswith("Amount: ") and body["message"] != "Request validation failed"
    assert body["details"]["errors"]  # the full list is still there


async def test_update_details_message_says_what_was_saved(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    async with h.client() as c:
        saved = await c.patch(f"/api/v1/bookings/{b['id']}/details", json={"customer_notes": "Gate code 4412"}, headers=s["mgr"]["h"])
        assert saved.status_code == 200, saved.text
        assert b["booking_number"] in saved.json()["message"] and "customer notes saved" in saved.json()["message"]
        nothing = await c.patch(f"/api/v1/bookings/{b['id']}/details", json={}, headers=s["mgr"]["h"])
        assert nothing.json()["message"].startswith("Nothing changed")


async def test_marking_an_unknown_whatsapp_conversation_read_is_a_404(db):
    adm = await h.admin(db)
    now = datetime.utcnow()
    await db.whatsapp_conversations.insert_one({"wa_id": "919000011122", "unread_count": 3, "created_at": now, "updated_at": now})
    async with h.client() as c:
        ghost = await c.post("/api/v1/whatsapp/crm/conversations/910000000000/read", headers=adm["h"])
        assert ghost.status_code == 404 and ghost.json()["error_code"] == "NOT_FOUND"
        real = await c.post("/api/v1/whatsapp/crm/conversations/919000011122/read", headers=adm["h"])
        assert real.status_code == 200 and real.json()["data"] == {"read": True}
    assert (await db.whatsapp_conversations.find_one({"wa_id": "919000011122"}))["unread_count"] == 0
