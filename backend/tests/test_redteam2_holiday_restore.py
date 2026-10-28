"""Red-team 2 (2026-10-28), RT-01: a booking deleted BEFORE a holiday was
declared on its date must not be restorable INTO the holiday. The restore of a
date outside any holiday, and of a done/cancelled job (no seat), is unchanged."""
import pytest

from tests import test_feat_booking_helpers as fb
from tests import test_feat_holidays_helpers as hh
from tests import test_fix_core_helpers as h
from tests.test_feat_holidays_booking import SLOTS, _rig
from tests.test_feat_holidays_helpers import clean  # noqa: F401 — fixture

pytestmark = pytest.mark.asyncio


async def test_restore_into_a_holiday_is_refused_and_changes_nothing(db, clean):
    s = await _rig(db, clean)
    b = await fb.book(db, s["cu"], hh.day(1), SLOTS[0])
    async with h.client() as c:
        assert (await c.post(f"/api/v1/bookings/{b['id']}/delete", headers=s["admin"])).status_code == 200
        await hh.make(c, s["admin"], scope="centers", service_center_ids=[s["center_id"]])
        r = await c.post(f"/api/v1/bookings/{b['id']}/restore", headers=s["admin"])
        assert r.status_code == 400, r.text
        assert "holiday" in r.json()["message"].lower()
    d = await fb.doc(db, b["id"])
    assert d["is_deleted"] is True and not d.get("holds_seat")  # still in the bin, no seat taken


async def test_restore_outside_the_holiday_still_works(db, clean):
    s = await _rig(db, clean)
    b = await fb.book(db, s["cu"], hh.day(3), SLOTS[0])
    async with h.client() as c:
        assert (await c.post(f"/api/v1/bookings/{b['id']}/delete", headers=s["admin"])).status_code == 200
        await hh.make(c, s["admin"], scope="centers", service_center_ids=[s["center_id"]], start_date=hh.day(1), end_date=hh.day(1))
        r = await c.post(f"/api/v1/bookings/{b['id']}/restore", headers=s["admin"])
        assert r.status_code == 200, r.text
    d = await fb.doc(db, b["id"])
    assert not d.get("is_deleted") and d["holds_seat"] is True
