"""Franchise share accrual (FranchiseService.sync_share): every transition of
a booking — complete, double sync, discount, cancel / refund / delete /
restore, captain reassign — plus share-% change, type switch and
concurrency. Accrual never touches Finance. ARCHITECTURE.md §7.13 D18."""
import asyncio
from datetime import timedelta

import pytest
from bson import ObjectId

from app.utils.timezone import now_ist
from tests import test_fix_core_helpers as h
from tests import test_franchise_helpers as fx
from tests.test_franchise_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


async def _franchise(c, admin, **kw) -> str:
    return (await fx.make_center(c, admin, fx.franchise_op(**kw)))["id"]


async def test_completion_accrues_share_and_double_sync_is_idempotent(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    await fx.sync(db, bid)
    b = await fx.booking(db, bid)
    assert b["franchise_share_percent"] == 70.0 and b["franchise_center_id"] == cid
    assert b["franchise_base_paise"] == 100000 and b["franchise_share_paise"] == 70000 and b["franchise_posted_paise"] == 70000
    assert b["franchise_rev"] == 1
    rows = await fx.ledger(db, cid)
    assert len(rows) == 1 and rows[0]["kind"] == "share" and rows[0]["amount_paise"] == 70000 and rows[0]["key"] == f"fr:{bid}:1"
    # Again and again: nothing more is posted.
    for _ in range(3):
        await fx.sync(db, bid)
    assert len(await fx.ledger(db, cid)) == 1 and (await fx.booking(db, bid))["franchise_rev"] == 1
    # Accrual is not cash: Finance is untouched.
    assert await db.finance_entries.count_documents({"service_center_id": cid}) == 0


async def test_rounding_half_up_to_the_paisa(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin, revenue_share_percent=33.33)
    bid = await fx.add_booking(db, cid, 349)  # 34900 * 33.33 % = 11632.17
    await fx.sync(db, bid)
    assert (await fx.booking(db, bid))["franchise_posted_paise"] == 11632


async def test_not_completed_or_other_centers_write_nothing(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        fr = await _franchise(c, admin)
        own = (await fx.make_center(c, admin))["id"]
    pending = await fx.add_booking(db, fr, 500, status="pending", payment_status="pending")
    selfop = await fx.add_booking(db, own, 500)
    await fx.sync(db, pending)
    await fx.sync(db, selfop)
    await fx.sync(db, str(ObjectId()))  # unknown booking: no error
    for bid in (pending, selfop):
        b = await fx.booking(db, bid)
        assert not any(k.startswith("franchise_") for k in b)
    assert await db.franchise_ledger.count_documents({"service_center_id": {"$in": [fr, own]}}) == 0


async def test_discount_after_completion_posts_a_delta(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    await fx.sync(db, bid)
    # BookingAdjust: total -100, discount +100.
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"total_amount": 900.0, "discount_amount": 100.0, "manager_discount": 100.0}})
    await fx.sync(db, bid)
    b = await fx.booking(db, bid)
    assert b["franchise_posted_paise"] == 63000 and b["franchise_rev"] == 2
    rows = await fx.ledger(db, cid)
    assert [(r["kind"], r["amount_paise"], r["key"]) for r in rows] == [("share", 70000, f"fr:{bid}:1"), ("share_adjust", -7000, f"fr:{bid}:2")]
    assert await fx.accrued(db, cid) == 63000
    # A tip is not part of the base.
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"total_amount": 950.0, "tip_amount": 50.0}})
    await fx.sync(db, bid)
    assert (await fx.booking(db, bid))["franchise_posted_paise"] == 63000


async def test_cancel_refund_delete_restore(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    await fx.sync(db, bid)
    oid = ObjectId(bid)
    steps = [
        ({"status": "cancelled"}, 0), ({"status": "completed"}, 70000),
        ({"payment_status": "refunded"}, 0), ({"payment_status": "paid"}, 70000),
        ({"payment_status": "refund_due"}, 0), ({"payment_status": "paid"}, 70000),
        ({"is_deleted": True}, 0), ({"is_deleted": False}, 70000),
        ({"status": "service_started"}, 0), ({"status": "completed"}, 70000),
    ]
    for change, expect in steps:
        await db.bookings.update_one({"_id": oid}, {"$set": change})
        await fx.sync(db, bid)
        b = await fx.booking(db, bid)
        assert b["franchise_posted_paise"] == expect and await fx.accrued(db, cid) == expect, change
    rows = await fx.ledger(db, cid)
    assert rows[0]["kind"] == "share" and all(r["kind"] == "share_adjust" for r in rows[1:])
    assert len({r["key"] for r in rows}) == len(rows)
    # The reversal row reads as one.
    assert "reversed" in rows[1]["note"]


async def test_reassign_captain_has_no_effect(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 800)
    await fx.sync(db, bid)
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"captain_id": "another-captain", "captain_earning": 99.0}})
    await fx.sync(db, bid)
    assert len(await fx.ledger(db, cid)) == 1 and (await fx.booking(db, bid))["franchise_rev"] == 1


async def test_share_percent_change_affects_only_future_bookings(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
        old = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, old)
        r = await fx.put_center(c, admin, cid, operation={"revenue_share_percent": 50})
        assert r.status_code == 200
        new = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, new)
        # The old booking keeps its frozen 70 %, even when it changes later.
        await db.bookings.update_one({"_id": ObjectId(old)}, {"$set": {"total_amount": 800.0, "subtotal": 800.0}})
        await fx.sync(db, old)
        # A future-dated change does not touch today's jobs.
        await fx.put_center(c, admin, cid, operation={"revenue_share_percent": 40, "effective_from": fx.day(3)})
        newer = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, newer)
    assert (await fx.booking(db, old))["franchise_share_percent"] == 70.0 and (await fx.booking(db, old))["franchise_posted_paise"] == 56000
    assert (await fx.booking(db, new))["franchise_share_percent"] == 50.0 and (await fx.booking(db, new))["franchise_posted_paise"] == 50000
    assert (await fx.booking(db, newer))["franchise_share_percent"] == 50.0
    # Jobs completed from the effective day use the new share.
    from app.services.franchise_service import operation_as_of

    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    assert operation_as_of(center, fx.day(3))["revenue_share_percent"] == 40


async def test_type_switch_applies_from_effective_day(db, clean):
    admin = await fx.admin_h(db)
    yesterday = now_ist() - timedelta(days=1)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin))["id"]  # self-operated
        before = await fx.add_booking(db, cid, 1000, when=yesterday)
        await fx.sync(db, before)
        r = await fx.put_center(c, admin, cid, operation={"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 60})
        assert r.status_code == 200
        # Completed before the switch: never a franchise job, even if it is edited afterwards.
        await db.bookings.update_one({"_id": ObjectId(before)}, {"$set": {"total_amount": 900.0}})
        await fx.sync(db, before)
        after = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, after)
        assert "franchise_share_percent" not in await fx.booking(db, before)
        assert (await fx.booking(db, after))["franchise_posted_paise"] == 60000
        # Back to self-operated: the franchise booking still adjusts (frozen); new jobs earn nothing.
        r = await fx.put_center(c, admin, cid, operation={"type": "self_operated"})
        assert r.status_code == 200
        await db.bookings.update_one({"_id": ObjectId(after)}, {"$set": {"status": "cancelled"}})
        await fx.sync(db, after)
        assert (await fx.booking(db, after))["franchise_posted_paise"] == 0
        later = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, later)
        assert "franchise_share_percent" not in await fx.booking(db, later)
    assert await fx.accrued(db, cid) == 0 and len(await fx.ledger(db, cid)) == 2


async def test_job_backdated_before_a_new_franchise_started_earns_no_share(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)  # created as a franchise: its effective day is today
    old = await fx.add_booking(db, cid, 1000, when=now_ist() - timedelta(days=3))
    today = await fx.add_booking(db, cid, 1000)
    await fx.sync(db, old)
    await fx.sync(db, today)
    assert "franchise_share_percent" not in await fx.booking(db, old)
    assert (await fx.booking(db, today))["franchise_posted_paise"] == 70000
    assert len(await fx.ledger(db, cid)) == 1


async def test_concurrent_syncs_post_once(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    await asyncio.gather(*[fx.sync(db, bid) for _ in range(6)])
    assert await fx.accrued(db, cid) == 70000
    assert len(await fx.ledger(db, cid)) == 1
    assert (await fx.booking(db, bid))["franchise_rev"] == 1


async def test_sync_inside_the_callers_transaction(db, clean):
    """The hook runs it in the completion's transaction: rolled back with it."""
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    async with await db.client.start_session() as s:
        async def _inside(session):
            await fx.sync(db, bid, session=session)
            raise RuntimeError("completion failed")
        with pytest.raises(RuntimeError):
            await s.with_transaction(_inside)
    assert await fx.accrued(db, cid) == 0 and "franchise_posted_paise" not in await fx.booking(db, bid)
    await fx.sync(db, bid)
    assert await fx.accrued(db, cid) == 70000


async def test_customer_and_captain_views_hide_franchise_fields():
    from app.services import booking_service as bs

    fields = ("franchise_share_percent", "franchise_share_paise", "franchise_posted_paise", "franchise_rev", "franchise_base_paise", "franchise_center_id")
    for hidden in (bs._CUSTOMER_HIDDEN_FIELDS, bs._CAPTAIN_HIDDEN_FIELDS):
        assert all(f in hidden for f in fields)


async def test_payroll_hook_drives_the_share(db, clean):
    """BACKEND A's one hook (after_booking_money_change) calls sync_share on
    every completion / edit path — inside the caller's transaction."""
    from app.services.payroll_hooks import after_booking_money_change

    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise(c, admin)
    bid = await fx.add_booking(db, cid, 1000)
    async with await db.client.start_session() as s:
        async def _inside(session):
            await after_booking_money_change(db, bid, session=session)
        await s.with_transaction(_inside)
    assert await fx.accrued(db, cid) == 70000
    await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"status": "cancelled"}})
    await after_booking_money_change(db, bid)
    assert await fx.accrued(db, cid) == 0
