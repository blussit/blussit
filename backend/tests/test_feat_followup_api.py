"""Follow-up 2026-10-07 — small API gaps the staff/captain UIs found:
the captain QR link answers in rupees (paise only as `amount_paise`), the
add-services response carries the enriched booking, the plan overviews show
when a pass stops being usable, charge views say where the money went, and
the manager's collections have a custom-plan line."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.services.booking_service import BookingService
from app.services.customer_charge_service import CustomerChargeService
from app.services.payment_service import PaymentService
from app.services.subscription_service import UserSubscriptionService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ (7) QR link


async def test_captain_link_amount_is_rupees_fresh_and_reused(db, monkeypatch):
    h.install_rzp_stub(monkeypatch)
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"captain_id": s["cap"]["id"], "status": "completed"}})
    due = (await fb.doc(db, b["id"]))["total_amount"]
    async with h.client() as c:
        fresh = await c.post("/api/v1/payments/collect/link", json={"booking_id": b["id"]}, headers=s["cap"]["h"])
        again = await c.post("/api/v1/payments/collect/link", json={"booking_id": b["id"]}, headers=s["cap"]["h"])
    assert fresh.status_code == 200 and again.status_code == 200, (fresh.text, again.text)
    fresh, again = fresh.json()["data"], again.json()["data"]
    assert again["link_id"] == fresh["link_id"]  # reused, not re-minted
    for link in (fresh, again):
        assert link["amount"] == pytest.approx(due) and link["amount_paise"] == int(round(due * 100))


# ------------------------------------------------------------- (8) add-services


async def test_add_services_returns_the_enriched_booking(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.hb.on_the_way(db, b["id"], s["cap"]["id"], vehicle_verified=True)
    polish = await db.services.find_one({"slug": "exterior-polish"})
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/add-services", json={"service_ids": [str(polish["_id"])]}, headers=s["cap"]["h"])
    assert r.status_code == 200, r.text
    booking = r.json()["data"]["booking"]
    d = await fb.doc(db, b["id"])
    for key in ("amount_due", "amount_paid", "wallet_applied", "wallet_due_carried", "added_services_total", "service_names", "customer_name"):
        assert key in booking, key
    assert booking["amount_due"] == d["total_amount"] and booking["total_amount"] == d["total_amount"]
    assert "platform_earning" not in booking and "service_code" not in booking  # still the captain's redaction


async def test_manager_add_services_sees_the_full_money_view(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    polish = await db.services.find_one({"slug": "exterior-polish"})
    out = await BookingService(db).add_services_on_site(b["id"], [str(polish["_id"])], {}, s["mgr"]["id"], "manager", s["center_id"])
    booking = out["booking"]
    assert booking["amount_due"] == out["added_total"] and booking["amount_paid"] == b["total_amount"]
    assert "platform_earning" in booking


# ------------------------------------------------------------ (10) overviews


async def test_plan_overview_rows_carry_usable_until_and_society(db):
    s = await fb.rig(db)
    sub_id = await fb.pass_for(db, s["cu"]["id"])
    await fb.book(db, s["cu"], s["when"], s["keys"][0])  # a customer this center served
    phone = (await db.users.find_one({"_id": ObjectId(s["cu"]["id"])}))["phone"]
    svc = UserSubscriptionService(db)
    center = await svc.center_overview(s["center_id"], "manager", s["center_id"], search=phone)
    admin = await svc.admin_overview(search=phone)
    view = next(v for v in await svc.list_for_customer(s["cu"]["id"], "admin", None) if v["id"] == sub_id)
    for result in (center, admin):
        row = next(r for r in result["rows"] if r["subscription_id"] == sub_id)
        assert row["usable_until"] and row["usable_until"] == view["usable_until"]
        assert row["last_bookable_day"] == view["last_bookable_day"]
        assert "society_id" in row and row["society_id"] is None
    await db.user_subscriptions.update_one({"_id": ObjectId(sub_id)}, {"$set": {"society_id": "soc-test-1"}})
    row = next(r for r in (await svc.admin_overview(search=phone))["rows"] if r["subscription_id"] == sub_id)
    assert row["society_id"] == "soc-test-1"


# ------------------------------------------------------------- (11) charges


async def test_charge_view_says_where_the_money_went(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, b["id"], 120)
    out = await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")
    charge_id = out["late_cancellation_charge"]["id"]
    svc = CustomerChargeService(db)
    view = await svc.view(await db.customer_charges.find_one({"_id": ObjectId(charge_id)}))
    assert view["settlement"] == "wallet" and view["wallet_credited"] == 0
    reduced = await svc.adjust(charge_id, 20, "goodwill", actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])
    assert reduced["settlement"] == "wallet" and reduced["wallet_credited"] == 30
    waived = await svc.adjust(charge_id, 0, None, actor_id=s["mgr"]["id"], actor_role="manager", actor_center_id=s["center_id"])
    assert waived["status"] == "waived" and waived["settlement"] == "wallet" and waived["wallet_credited"] == 50
    rows, _ = await svc.list_for_staff(actor_role="manager", actor_center_id=s["center_id"], status=None, customer_id=s["cu"]["id"], page=1, page_size=20)
    assert rows[0]["wallet_credited"] == 50 and rows[0]["settlement"] == "wallet"
    mine = await svc.my_charges(s["cu"]["id"])
    assert mine["items"][0]["settlement"] == "wallet" and mine["items"][0]["wallet_credited"] == 50
    # A charge adjusted before the field existed: read from its history.
    legacy = await db.customer_charges.find_one({"_id": ObjectId(charge_id)})
    legacy.pop("wallet_credited")
    assert CustomerChargeService.wallet_credited_of(legacy) == 50


async def test_charge_carried_by_a_booking_or_still_open(db):
    assert CustomerChargeService.settlement_of({"status": "applied", "applied_to_booking_id": "x"}) == "booking"
    assert CustomerChargeService.settlement_of({"status": "open"}) is None
    assert CustomerChargeService.wallet_credited_of({"status": "applied", "applied_to_booking_id": "x",
                                                     "history": [{"action": "reduced", "from": 80, "to": 50}]}) == 0


# ---------------------------------------------------------- (12) collections


async def test_manager_collections_have_their_custom_plan_line_and_no_wallet(db, cleanup):
    s = await fb.rig(db)
    other_center, _ = await h.center(db)
    now = datetime.now(timezone.utc)
    rows = [
        {"kind": "cash", "amount_paise": 120000, "service_center_id": s["center_id"]},
        {"kind": "link", "amount_paise": 80000, "service_center_id": s["center_id"]},
        {"kind": "link", "amount_paise": 50000, "service_center_id": s["center_id"], "refunded_amount_paise": 50000},
        {"kind": "cash", "amount_paise": 99900, "service_center_id": other_center},
        # Not paid (a cancelled link) — never counted. Not "created": an open
        # link here would be picked up by other suites' link sweeps.
        {"kind": "link", "amount_paise": 70000, "service_center_id": s["center_id"], "status": "cancelled"},
    ]
    tag = str(ObjectId())
    cleanup.append(("payment_orders", {"test_tag": tag}))
    await db.payment_orders.insert_many([
        {"purpose": "custom_plan", "status": "paid", "currency": "INR", "customer_id": s["cu"]["id"], "created_at": now,
         "custom_plan_id": str(ObjectId()), "test_tag": tag, **r}
        for r in rows
    ])
    payments = PaymentService(db)
    mine = await payments.center_collections(s["center_id"], "manager", s["center_id"], None, None)
    assert mine["wallet"] is None
    assert mine["custom_plans"] == {"online_amount": 800.0, "cash_amount": 1200.0, "count": 3, "cash_count": 1}
    async with h.client() as c:
        r = await c.get(f"/api/v1/payments/collections/center/{s['center_id']}", headers=s["mgr"]["h"])
    assert r.status_code == 200 and r.json()["data"]["custom_plans"]["cash_amount"] == 1200.0
    admin = await payments.admin_collections(None, None)
    assert set(admin["custom_plans"]) == set(mine["custom_plans"]) and admin["wallet"] is not None
    assert admin["custom_plans"]["cash_amount"] >= 1200.0 + 999.0
