"""Founder rule: archiving (delete) or pausing a service center never deletes
or hides a record. Franchise page / statements / settlements, Finance filter,
Bookings-by-center, audit names and name lookups keep working for a paused or
archived center; pickers for NEW work leave them out; the switch between
self-operated and franchise is a full timestamp (only jobs completed at / after
the switch use the new terms); a completed booking never changes center."""
from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_adjust_schema import AdminAddress, AdminBookingEditRequest
from app.services import report_cache
from app.services.booking_admin_edit_service import BookingAdminEditService
from app.services.franchise_service import FranchiseService, effective_instant, operation_as_of
from app.utils.timezone import IST, now_ist
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h
from tests import test_franchise_helpers as fx
from tests.test_franchise_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


async def _franchise_center(c, admin) -> str:
    return (await fx.make_center(c, admin, fx.franchise_op()))["id"]


async def _archive(c, admin, cid):
    return await c.delete(f"{fx.CENTERS}/{cid}", headers=admin)


# ------------------------------------------------------------ archive keeps records


async def test_archived_franchise_center_keeps_every_record(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise_center(c, admin)
        bid = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, bid)
        settle = await c.post(f"{fx.BASE}/centers/{cid}/settlements", json={
            "kind": "pay_partner", "amount_paise": 10000, "method": "cash", "idempotency_key": "arch-key-0001"}, headers=admin)
        assert settle.status_code == 200, settle.text

        r = await _archive(c, admin, cid)
        assert r.status_code == 200, r.text
        center = await db.service_centers.find_one({"_id": ObjectId(cid)})
        assert center["is_deleted"] is True and center["is_active"] is False and center.get("archived_at")
        # Nothing was deleted anywhere.
        assert await db.bookings.count_documents({"_id": ObjectId(bid)}) == 1
        assert await db.franchise_ledger.count_documents({"service_center_id": cid}) == 2
        assert await db.finance_entries.count_documents({"service_center_id": cid}) >= 1

        # Franchise page: the center is still listed, labelled Archived, money intact.
        listed = (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"]
        row = next(x for x in listed if x["id"] == cid)
        assert row["status"] == "archived" and row["status_label"] == "Archived" and row["share_accrued_paise"] == 70000
        st = await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)
        assert st.status_code == 200, st.text
        data = st.json()["data"]
        assert data["center"]["status"] == "archived" and len(data["settlements"]) == 1 and len(data["ledger"]) == 2
        # A final settlement with an archived center is still possible; so is voiding.
        more = await c.post(f"{fx.BASE}/centers/{cid}/settlements", json={
            "kind": "pay_partner", "amount_paise": 5000, "method": "cash", "idempotency_key": "arch-key-0002"}, headers=admin)
        assert more.status_code == 200, more.text
        void = await c.post(f"{fx.BASE}/centers/{cid}/settlements/{more.json()['data']['id']}/void", json={"reason": "Typo in the amount"}, headers=admin)
        assert void.status_code == 200, void.text
        # A completed job can still be re-synced (a late edit) at an archived center.
        await db.bookings.update_one({"_id": ObjectId(bid)}, {"$set": {"total_amount": 800.0, "subtotal": 800.0}})
        await fx.sync(db, bid)
        assert (await fx.booking(db, bid))["franchise_posted_paise"] == 56000

        # Finance filtered by that center still returns its entries.
        fin = await c.get("/api/v1/admin/finance/entries", params={"service_center_id": cid}, headers=admin)
        assert fin.status_code == 200, fin.text
        assert fin.json()["data"] and all(e["service_center_id"] == cid for e in fin.json()["data"])

        # Admin list: hidden by default, shown (with its status) for history views.
        default = await c.get(fx.CENTERS, params={"page_size": 100}, headers=admin)
        assert cid not in [x["id"] for x in default.json()["data"]]
        hist = await c.get(fx.CENTERS, params={"page_size": 100, "include_archived": True}, headers=admin)
        arch = next(x for x in hist.json()["data"] if x["id"] == cid)
        assert arch["status"] == "archived"
        # Pickers for new work never offer it, even with include_archived.
        pick = await c.get(fx.CENTERS, params={"page_size": 100, "active_only": True, "include_archived": True}, headers=admin)
        assert cid not in [x["id"] for x in pick.json()["data"]]
        # It still opens, and the audit trail names it.
        assert (await c.get(f"{fx.CENTERS}/{cid}", headers=admin)).status_code == 200
        logs = await c.get("/api/v1/audit-logs", params={"page_size": 50}, headers=admin)
        assert logs.status_code == 200
        names = {x.get("service_center_name") for x in logs.json()["data"] if x.get("service_center_id") == cid}
        assert names and None not in names

        # Restore: back, paused.
        back = await c.post(f"{fx.CENTERS}/{cid}/restore", headers=admin)
        assert back.status_code == 200, back.text
        assert back.json()["data"]["status"] == "paused"
        assert (await c.post(f"{fx.CENTERS}/{cid}/restore", headers=admin)).status_code == 404


async def test_paused_center_stays_in_history_views_but_not_in_pickers(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise_center(c, admin)
        bid = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, bid)
        r = await fx.put_center(c, admin, cid, is_active=False)
        assert r.status_code == 200 and r.json()["data"]["status"] == "paused"
        listed = (await c.get(f"{fx.BASE}/centers", headers=admin)).json()["data"]["items"]
        row = next(x for x in listed if x["id"] == cid)
        assert row["status"] == "paused" and row["share_accrued_paise"] == 70000
        everyone = (await c.get(fx.CENTERS, params={"page_size": 100}, headers=admin)).json()["data"]
        assert next(x for x in everyone if x["id"] == cid)["status"] == "paused"
        pick = (await c.get(fx.CENTERS, params={"page_size": 100, "active_only": True}, headers=admin)).json()["data"]
        assert cid not in [x["id"] for x in pick]
        # Bookings by center: a paused center that has bookings keeps its card.
        report_cache.invalidate("analytics")
        cards = (await c.get("/api/v1/analytics/service-centers", headers=admin)).json()["data"]
        card = next(x for x in cards if x["service_center_id"] == cid)
        assert card["status"] == "paused" and card["bookings"] == 1


async def test_bookings_by_center_keep_archived_with_history_and_drop_empty_ones(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        busy = (await fx.make_center(c, admin))["id"]
        empty = (await fx.make_center(c, admin))["id"]
        await fx.add_booking(db, busy, 500)
        for cid in (busy, empty):
            assert (await _archive(c, admin, cid)).status_code == 200
        report_cache.invalidate("analytics")
        cards = {x["service_center_id"]: x for x in (await c.get("/api/v1/analytics/service-centers", headers=admin)).json()["data"]}
        assert cards[busy]["status"] == "archived" and cards[busy]["bookings"] == 1
        assert empty not in cards


async def test_archive_is_refused_while_live_work_depends_on_it_and_pause_is_always_possible(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin))["id"]
        await db.bookings.insert_one(fx.completed_booking(cid, 500, status="pending", payment_status="pending"))
        r = await _archive(c, admin, cid)
        assert r.status_code == 400 and "archived" in r.text and "live booking" in r.text
        assert (await db.service_centers.find_one({"_id": ObjectId(cid)})).get("is_deleted") is not True
        assert (await fx.put_center(c, admin, cid, is_active=False)).status_code == 200


async def test_name_lookups_resolve_archived_and_paused_centers(db, clean):
    from app.repositories.service_center_repository import ServiceCenterRepository, center_label

    admin = await fx.admin_h(db)
    async with h.client() as c:
        arch = await fx.make_center(c, admin)
        paused = await fx.make_center(c, admin)
        await _archive(c, admin, arch["id"])
        await fx.put_center(c, admin, paused["id"], is_active=False)
    rows = await ServiceCenterRepository(db).find_by_ids([arch["id"], paused["id"]])
    assert {str(r["_id"]) for r in rows} == {arch["id"], paused["id"]}
    labels = {str(r["_id"]): center_label(r) for r in rows}
    assert labels[arch["id"]].endswith("(Archived)") and labels[paused["id"]].endswith("(Paused)")


# ------------------------------------------------------------ switch moment


async def test_switch_to_franchise_counts_only_jobs_completed_after_the_switch_moment(db, clean):
    admin = await fx.admin_h(db)
    earlier_today = now_ist() - timedelta(minutes=40)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin))["id"]  # self-operated
        before = await fx.add_booking(db, cid, 1000, when=earlier_today)
        r = await fx.put_center(c, admin, cid, operation={"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 60})
        assert r.status_code == 200, r.text
        after = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, before)
        await fx.sync(db, after)
        # An edit to the morning job later does not turn it into a franchise job either.
        await db.bookings.update_one({"_id": ObjectId(before)}, {"$set": {"total_amount": 900.0, "subtotal": 900.0}})
        await fx.sync(db, before)
    assert "franchise_share_percent" not in await fx.booking(db, before)
    assert (await fx.booking(db, after))["franchise_posted_paise"] == 60000
    assert await fx.accrued(db, cid) == 60000
    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    entry = center["operation_history"][0]
    assert entry["effective_at"] is not None and entry["effective_from"] == fx.today()
    # The recorded moment separates the two jobs.
    assert operation_as_of(center, earlier_today)["type"] == "self_operated"
    assert operation_as_of(center, now_ist() + timedelta(minutes=1))["type"] == "franchise"


async def test_switch_back_to_self_operated_mid_day_keeps_earlier_jobs_franchise(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise_center(c, admin)
        morning = await fx.add_booking(db, cid, 1000, when=now_ist() - timedelta(minutes=40))
        r = await fx.put_center(c, admin, cid, operation={"type": "self_operated"})
        assert r.status_code == 200, r.text
        evening = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, morning)
        await fx.sync(db, evening)
    assert (await fx.booking(db, morning))["franchise_posted_paise"] == 70000
    assert "franchise_share_percent" not in await fx.booking(db, evening)


async def test_effective_instant_rules():
    now = now_ist().replace(hour=15, minute=20, second=7, microsecond=0)
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    # No date, or today: the switch is NOW.
    assert effective_instant(None, now) == now and effective_instant(today, now) == now
    # Another day (future, or a backdated one that is explicitly picked): that day's START (IST).
    assert effective_instant(tomorrow, now) == (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    assert effective_instant(yesterday, now) == (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)
    assert effective_instant(tomorrow, now).tzinfo is not None and effective_instant(tomorrow, now).utcoffset() == IST.utcoffset(None)


async def test_future_dated_switch_starts_at_the_start_of_that_day(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin))["id"]
        r = await fx.put_center(c, admin, cid, operation={
            "type": "franchise", "partner": fx.partner(), "revenue_share_percent": 50, "effective_from": fx.day(2)})
        assert r.status_code == 200, r.text
        today_job = await fx.add_booking(db, cid, 1000)
        await fx.sync(db, today_job)
        assert "franchise_share_percent" not in await fx.booking(db, today_job)
    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    start = effective_instant(fx.day(2))
    assert operation_as_of(center, start - timedelta(seconds=1))["type"] == "self_operated"
    assert operation_as_of(center, start)["type"] == "franchise"


async def test_cash_held_on_the_switch_day_counts_only_after_the_switch(db, clean):
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = (await fx.make_center(c, admin))["id"]
        await fx.add_booking(db, cid, 1000, when=now_ist() - timedelta(minutes=40))     # cash, before the switch
        r = await fx.put_center(c, admin, cid, operation={"type": "franchise", "partner": fx.partner(), "revenue_share_percent": 70})
        assert r.status_code == 200, r.text
        await fx.add_booking(db, cid, 500)                                                   # cash, after the switch
        st = (await c.get(f"{fx.BASE}/centers/{cid}/statement", headers=admin)).json()["data"]
    assert st["all_time"]["cash_collected_paise"] == 50000


async def test_legacy_history_rows_without_a_moment_still_work(db, clean):
    """A row saved before `effective_at` existed falls back to the start of its day."""
    admin = await fx.admin_h(db)
    async with h.client() as c:
        cid = await _franchise_center(c, admin)
        await fx.put_center(c, admin, cid, operation={"revenue_share_percent": 60, "effective_from": fx.day(3)})
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$unset": {"operation_history.0.effective_at": ""}})
    center = await db.service_centers.find_one({"_id": ObjectId(cid)})
    assert operation_as_of(center, fx.day(3))["revenue_share_percent"] == 60
    assert operation_as_of(center, effective_instant(fx.day(3)) - timedelta(seconds=1))["revenue_share_percent"] == 70
    assert operation_as_of(center, effective_instant(fx.day(3)))["revenue_share_percent"] == 60


# ------------------------------------------------------------ completed bookings never change center


async def _two_centers(db):
    a = await fb.rig(db)
    cid_b, pin_b = await h.center(db, latitude=23.55, longitude=76.55)
    return a, cid_b, pin_b


def _move_to(pin_b: str, **kw) -> AdminBookingEditRequest:
    return AdminBookingEditRequest(
        reason="Customer moved to the other side of town", recompute_center=True,
        address=AdminAddress(line1="12 New Road", pincode=pin_b, latitude=23.55, longitude=76.55), **kw,
    )


async def test_an_open_booking_can_change_center_but_a_completed_paid_one_cannot(db):
    s, cid_b, pin_b = await _two_centers(db)
    adm = await h.admin(db)
    actor = {"id": adm["id"], "role": "admin"}
    svc = BookingAdminEditService(db)

    open_b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    moved = await svc.edit(open_b["id"], _move_to(pin_b), actor)
    assert (await fb.doc(db, open_b["id"]))["service_center_id"] == cid_b and moved["changes"]

    done = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    await db.bookings.update_one({"_id": ObjectId(done["id"])}, {"$set": {"status": "completed", "payment_status": "paid", "completed_at": now_ist()}})
    with pytest.raises(BadRequestException) as exc:
        await svc.edit(done["id"], _move_to(pin_b), actor)
    assert "completed" in exc.value.message.lower()
    assert (await fb.doc(db, done["id"]))["service_center_id"] == s["center_id"]


async def test_a_completed_car_in_a_visit_is_never_dragged_to_another_center(db):
    s, cid_b, pin_b = await _two_centers(db)
    adm = await h.admin(db)
    actor = {"id": adm["id"], "role": "admin"}
    svc = BookingAdminEditService(db)
    first = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    second = await fb.book(db, s["cu"], s["when"], s["keys"][1])
    group = "visit-" + first["id"]
    await db.bookings.update_many({"_id": {"$in": [ObjectId(first["id"]), ObjectId(second["id"])]}}, {"$set": {"booking_group_id": group}})
    await db.bookings.update_one({"_id": ObjectId(second["id"])}, {"$set": {"status": "completed", "payment_status": "paid", "completed_at": now_ist()}})
    # Editing the OPEN car of a visit that has a completed car is refused as a whole …
    with pytest.raises(BadRequestException) as exc:
        await svc.edit(first["id"], _move_to(pin_b), actor)
    assert "completed" in exc.value.message.lower()
    # … and the completed car's center is untouched.
    assert (await fb.doc(db, second["id"]))["service_center_id"] == s["center_id"]
    assert (await fb.doc(db, first["id"]))["service_center_id"] == s["center_id"]


async def test_captain_reassign_is_refused_for_completed_bookings(db):
    from app.schemas.booking_schema import ReassignCaptainRequest
    from app.services.booking_service import BookingService

    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"status": "completed", "payment_status": "paid"}})
    other = await h.captain(db, s["center_id"], wallet=500.0)
    with pytest.raises(BadRequestException):
        await BookingService(db).reassign_captain(b["id"], ReassignCaptainRequest(captain_id=other["id"]), s["mgr"]["id"], "manager", s["center_id"])
