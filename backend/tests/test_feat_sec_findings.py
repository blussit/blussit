"""SECURITY-REVIEW 2026-10-07 — regressions for the issues the review of
the wallet / edits / add-ons / plans round found (each written failing
first, then fixed):

  SEC-1  the new captain-wallet bookkeeping fields on a booking
         (captain_wallet_posted = his earning − cash he holds,
         captain_cash_collected, captain_wallet_captain_id) reached the
         CUSTOMER's booking views — i.e. the captain's pay, which every
         customer view hides (captain_earning).
  SEC-2  a customer's own cancel answered with the raw booking document —
         platform_earning / captain_earning and the other fields every other
         customer read redacts.
  SEC-3  the universal message (staff free text → a customer's WhatsApp
         under the Blussit name) had no cap: any manager could flood a
         customer of their center (each send is a paid Meta conversation).
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.services.money_service import MoneyService
from app.services.notification_service import UNIVERSAL_MESSAGE_DAILY_CAP
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio

_CAPTAIN_PAY_FIELDS = ("captain_earning", "captain_wallet_posted", "captain_cash_collected", "captain_wallet_captain_id", "platform_earning")


async def test_sec1_customer_views_hide_the_captain_wallet_fields(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    await db.bookings.update_one(
        {"_id": ObjectId(b["id"])},
        {"$set": {"captain_id": s["cap"]["id"], "status": "completed", "closed_at": datetime.now(timezone.utc)}},
    )
    await MoneyService(db).settle_captain_wallet([b["id"]])
    stored = await fb.doc(db, b["id"])
    assert stored.get("captain_wallet_posted")  # the leak needs the field to exist
    async with h.client() as c:
        one = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["cu"]["h"])).json()["data"]
        listed = (await c.get("/api/v1/bookings/my", headers=s["cu"]["h"])).json()["data"]
        staff = (await c.get(f"/api/v1/bookings/{b['id']}", headers=s["mgr"]["h"])).json()["data"]
    row = next(x for x in listed if x["id"] == b["id"])
    for view in (one, row):
        for field in _CAPTAIN_PAY_FIELDS:
            assert field not in view, field
    assert staff.get("captain_wallet_posted") == stored["captain_wallet_posted"]  # staff still see it


async def test_sec2_customer_cancel_response_is_redacted(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b['id']}/cancel", json={"reason": "changed plans"}, headers=s["cu"]["h"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "cancelled" and "wallet" in data
    for field in ("captain_earning", "platform_earning", "captain_travel_pay", "captain_service_pay"):
        assert field not in data, field
    # Staff cancels still answer with the full document.
    b2 = await fb.book(db, s["cu"], s["when"], s["keys"][1] if len(s["keys"]) > 1 else s["keys"][0])
    async with h.client() as c:
        r = await c.post(f"/api/v1/bookings/{b2['id']}/cancel", json={"reason": "no captain free"}, headers=s["mgr"]["h"])
    assert r.status_code == 200 and "platform_earning" in r.json()["data"], r.text


async def test_sec3_universal_message_is_capped_per_customer_per_day(db):
    s = await fb.rig(db)
    await fb.book(db, s["cu"], s["when"], s["keys"][0])  # known to the center
    admin = await h.admin(db)
    url = "/api/v1/notifications/universal-message"
    async with h.client() as c:
        for n in range(UNIVERSAL_MESSAGE_DAILY_CAP):
            who = s["mgr"]["h"] if n % 2 else admin["h"]
            r = await c.post(url, json={"customer_id": s["cu"]["id"], "message": f"Update number {n}"}, headers=who)
            assert r.status_code == 200, (n, r.text)
        r = await c.post(url, json={"customer_id": s["cu"]["id"], "message": "One more"}, headers=s["mgr"]["h"])
        assert r.status_code == 429, r.text
        r = await c.post(url, json={"customer_id": s["cu"]["id"], "message": "Admin too"}, headers=admin["h"])
        assert r.status_code == 429, r.text
        # Another customer is unaffected.
        other = await fb.rig(db)
        r = await c.post(url, json={"customer_id": other["cu"]["id"], "message": "Hello"}, headers=admin["h"])
        assert r.status_code == 200, r.text
    refused = await db.audit_logs.count_documents({"action": "WHATSAPP_UNIVERSAL_MESSAGE", "target_id": s["cu"]["id"]})
    assert refused == UNIVERSAL_MESSAGE_DAILY_CAP  # refused sends leave no "sent" audit row


async def test_sec4_double_tapped_add_on_is_added_once(db):
    """SEC-4: two identical add-service requests at once (a double tap, a
    retry) both passed the mix check before either wrote, and the loser's
    transaction retry then added the same one-per-car add-on a second time
    — the customer was charged twice."""
    import asyncio

    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.hb.on_the_way(db, b["id"], s["cap"]["id"], vehicle_verified=True)
    polish = await db.services.find_one({"slug": "exterior-polish"})
    body = {"service_ids": [str(polish["_id"])]}
    for _round in range(3):
        async with h.client() as c:
            results = await asyncio.gather(*[
                c.post(f"/api/v1/bookings/{b['id']}/add-services", json=body, headers=s["cap"]["h"]) for _ in range(3)
            ])
        if any(r.status_code == 200 for r in results):
            break
    ok = [r for r in results if r.status_code == 200]
    assert len(ok) <= 1, [r.text for r in results]
    after = await fb.doc(db, b["id"])
    added = [e for e in after.get("added_services") or [] if e["service_id"] == str(polish["_id"])]
    assert len(added) == 1, added
    assert after["total_amount"] == pytest.approx(b["total_amount"] + added[0]["amount"])



async def test_sec2b_customer_reschedule_response_is_redacted(db):
    """Reschedule, like cancel, answers the customer with the booking — no
    platform/captain earnings in it."""
    from datetime import timedelta

    from app.schemas.booking_schema import BookingRescheduleRequest
    from app.services.booking_service import BookingService
    from app.utils.timezone import now_ist

    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    later = (now_ist().date() + timedelta(days=3)).isoformat()
    moved = await BookingService(db).reschedule_booking(
        b["id"], BookingRescheduleRequest(scheduled_date=later, scheduled_slot=s["keys"][0]), s["cu"]["id"], "customer")
    for field in ("captain_earning", "platform_earning", "captain_travel_pay", "captain_service_pay"):
        assert field not in moved, field
