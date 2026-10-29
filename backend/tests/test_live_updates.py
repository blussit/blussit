"""Live updates (2026-10-29): a change anyone makes reaches every screen that
shows it.

Pins (1) which websocket channels each kind of write pings — the customer's
own `user:{id}`, the center's `center-bookings:{center}`, the admin-only
`admin:live` — (2) who may subscribe, and (3) that the numbers the screens
refetch are right the moment the ping arrives: a cancelled booking leaves the
manager's board and counts, a rescheduled one appears once, at its new time.
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from bson import ObjectId

from app.core.ws_manager import ADMIN_CHANNEL, manager as ws
from app.routes.v1.ws_routes import _WsUser, _authorize_channel
from app.schemas.booking_schema import BookingRescheduleRequest
from app.services.booking_service import BookingService
from app.services.customer_wallet_service import CustomerWalletService
from app.services.kpi_service import resolve_period
from app.services.manager_dashboard_service import ManagerDashboardService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


class Pings:
    """Every (channel, message) the code under test broadcast."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def __call__(self, channel, message):
        self.sent.append((channel, message))

    def channels(self, kind=None):
        return {c for c, m in self.sent if kind is None or m.get("kind") == kind}


def listening():
    pings = Pings()
    return pings, patch.object(ws, "broadcast", pings)


# ---------------------------------------------------------------- bookings


async def test_cancel_pings_the_customer_the_center_the_booking_and_admins(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    pings, ctx = listening()
    with ctx:
        await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")
    assert {f"user:{s['cu']['id']}", f"center-bookings:{s['center_id']}", f"booking:{b['id']}", ADMIN_CHANNEL} <= pings.channels("booking")
    assert all(m["booking_id"] == b["id"] for c, m in pings.sent if m.get("kind") == "booking")


async def test_reschedule_pings_the_customer_the_center_and_admins(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    pings, ctx = listening()
    with ctx:
        await BookingService(db).reschedule_booking(
            b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=s["keys"][1]), s["mgr"]["id"], "manager", s["center_id"],
        )
    assert {f"user:{s['cu']['id']}", f"center-bookings:{s['center_id']}", ADMIN_CHANNEL} <= pings.channels("booking")
    assert f"slots:{s['center_id']}:{s['when']}" in pings.channels()


async def test_assigning_a_captain_pings_the_captain_too(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    pings, ctx = listening()
    with ctx:
        await fb.assign(db, b["id"], s)
    assert f"user:{s['cap']['id']}" in pings.channels("booking")
    assert f"user:{s['cu']['id']}" in pings.channels("booking")


async def test_a_payment_on_a_booking_pings_everyone_watching_it(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0], method="online")
    pings, ctx = listening()
    with ctx:
        await fb.pay_online(db, b["id"])
    assert {f"user:{s['cu']['id']}", f"booking:{b['id']}", f"center-bookings:{s['center_id']}", ADMIN_CHANNEL} <= pings.channels("booking")


async def test_a_notification_pings_only_its_recipient(db):
    from app.services.notification_service import NotificationService

    s = await fb.rig(db)
    pings, ctx = listening()
    with ctx:
        await NotificationService(db).notify(s["cu"]["id"], "Hello", "A note", send_whatsapp=False)
    assert pings.channels("notification") == {f"user:{s['cu']['id']}"}


# ------------------------------------------------------------ wallet / plans


async def test_wallet_movement_pings_the_customer(db):
    s = await fb.rig(db)
    pings, ctx = listening()
    with ctx:
        await CustomerWalletService(db).post(s["cu"]["id"], 50, "adjustment", key="live_wallet_1", note="test")
    assert f"user:{s['cu']['id']}" in pings.channels("wallet")
    # The same key again changes nothing, so it says nothing.
    again, ctx = listening()
    with ctx:
        await CustomerWalletService(db).post(s["cu"]["id"], 50, "adjustment", key="live_wallet_1", note="test")
    assert not again.sent


async def test_buying_and_using_a_plan_pings_the_customer_and_the_admin_feed(db):
    s = await fb.rig(db)
    pings, ctx = listening()
    with ctx:
        sub_id = await fb.pass_for(db, s["cu"]["id"])
    assert {f"user:{s['cu']['id']}", ADMIN_CHANNEL} <= pings.channels("plan")
    used, ctx = listening()
    with ctx:
        from app.services.subscription_service import UserSubscriptionService

        await UserSubscriptionService(db).cancel(s["cu"]["id"], sub_id)
    assert f"user:{s['cu']['id']}" in used.channels("plan")


# ---------------------------------------------------------------- channels


async def test_only_the_admin_may_listen_to_the_admin_feed_and_nobody_to_someone_elses_channel(db):
    s = await fb.rig(db)
    admin = await h.admin(db)
    me = _WsUser(id=s["cu"]["id"], role="customer", service_center_id=None)
    boss = _WsUser(id=admin["id"], role="admin", service_center_id=None)
    manager = _WsUser(id=s["mgr"]["id"], role="manager", service_center_id=s["center_id"])
    assert await _authorize_channel(ADMIN_CHANNEL, boss, db) is True
    assert await _authorize_channel(ADMIN_CHANNEL, me, db) is False
    assert await _authorize_channel(ADMIN_CHANNEL, manager, db) is False
    assert await _authorize_channel(f"user:{me.id}", me, db) is True
    assert await _authorize_channel(f"user:{boss.id}", me, db) is False
    assert await _authorize_channel("admin:other", boss, db) is False


async def test_the_admin_feed_expires_the_report_caches(db):
    from app.services import report_cache

    calls = []

    async def compute():
        calls.append(1)
        return len(calls)

    assert await report_cache.cached(("kpi", "x"), 60, compute) == 1
    assert await report_cache.cached(("kpi", "x"), 60, compute) == 1
    from app.core import ws_manager as wm

    wm._report_expired_at = 0.0
    await ws._deliver_local(ADMIN_CHANNEL, {"type": "changed"})
    assert await report_cache.cached(("kpi", "x"), 60, compute) == 2
    report_cache.invalidate("kpi")


# --------------------------------------------- the numbers behind the screens


async def _board(db, s):
    a, b, c, d = resolve_period("today", None, None)
    return await ManagerDashboardService(db).dashboard(s["center_id"], a, b, c, d)


async def _my_bookings(s):
    async with h.client() as c:
        r = await c.get("/api/v1/bookings/my?page=1&page_size=50", headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        return r.json()["data"]


async def test_cancelled_booking_leaves_the_managers_dashboard_and_the_customers_active_list(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    # Make it a "today" booking so the board sees it.
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"scheduled_date": datetime.strptime(h.day(0), "%Y-%m-%d")}})
    before = await _board(db, s)
    assert before["today"]["cars"] == 1 and before["ops"]["needs_captain"] == 1

    await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")

    after = await _board(db, s)
    assert after["today"]["cars"] == 0 and after["today"]["visits"] == 0
    assert after["ops"]["needs_captain"] == 0
    rows = await _my_bookings(s)
    mine = [r for r in rows if r["id"] == b["id"]]
    assert len(mine) == 1 and mine[0]["status"] == "cancelled"
    # The seat went back, so the slot picker (refetched on the slots ping) shows room.
    assert (await h.seat(db, s["center_id"], s["when"], s["keys"][0])).get("booked_count", 0) == 0


async def test_rescheduled_booking_shows_once_at_its_new_time(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await BookingService(db).reschedule_booking(
        b["id"], BookingRescheduleRequest(scheduled_date=s["when"], scheduled_slot=s["keys"][1]), s["cu"]["id"], "customer", None,
    )
    rows = await _my_bookings(s)
    mine = [r for r in rows if r["id"] == b["id"]]
    assert len(mine) == 1
    assert mine[0]["scheduled_slot"] == s["keys"][1] and mine[0]["status"] in ("rescheduled", "pending")
    assert len([r for r in rows if r.get("booking_number") == b["booking_number"]]) == 1
    # One live booking in the new slot, none left in the old one.
    assert await h.live(db, s["center_id"], s["when"], s["keys"][1]) == 1
    assert await h.live(db, s["center_id"], s["when"], s["keys"][0]) == 0
