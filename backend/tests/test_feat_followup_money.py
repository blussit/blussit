"""Follow-up 2026-10-07 — manager discount and tip vs the booking money
fields (spec 1.5). A manager discount exists only on "Log A Done Job" (a
new, already-completed booking whose money is recorded AFTER the discount,
and which never meets the customer wallet); a tip exists only on a job the
manager did, and moves total and amount_paid together. So neither can
leave money due, or paid twice, on a booking that was prepaid online."""
import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import ManagerLogBookingRequest, ManagerMarkDoneRequest, QuickBookingLine
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _on_the_bookings_day(monkeypatch):
    """Mark-done is allowed on the booking's own day (MGR-06); these
    bookings are two days out."""
    monkeypatch.setattr("app.services.booking_service._ist_today", lambda: "2999-12-31")


async def _phone(db, customer_id: str) -> str:
    return (await db.users.find_one({"_id": ObjectId(customer_id)}))["phone"]


async def _prepaid_marked_done(db, s: dict, *, wallet_credit: float = 0.0) -> dict:
    if wallet_credit:
        await fb.credit(db, s["cu"]["id"], wallet_credit)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    paid = await fb.doc(db, b["id"])
    assert paid["payment_status"] == "paid" and paid["amount_due"] == 0
    await BookingService(db).manager_mark_done(b["id"], s["mgr"]["id"], "manager", s["center_id"], send_whatsapp=False)
    done = await fb.doc(db, b["id"])
    assert done["status"] == "completed" and done["completed_by_role"] == "manager"
    # Nothing was due, so the manager collected nothing.
    assert done["amount_paid"] == paid["amount_paid"] and done["payment_status"] == "paid"
    return done


async def test_tip_on_a_prepaid_booking_creates_no_due(db):
    s = await fb.rig(db)
    done = await _prepaid_marked_done(db, s)
    balance = await fb.balance(db, s["cu"]["id"])
    svc = BookingService(db)
    await svc.set_tip(str(done["_id"]), 50, s["mgr"]["id"], "manager", s["center_id"])
    d = await fb.doc(db, str(done["_id"]))
    assert d["tip_amount"] == 50
    assert d["total_amount"] == pytest.approx(done["total_amount"] + 50)
    assert d["amount_paid"] == pytest.approx(done["amount_paid"] + 50)
    assert d["payment_status"] == "paid" and d["amount_due"] == 0
    from app.services import booking_money as bm

    assert bm.amount_due(d) == 0 and bm.money_view(d)["payment_status"] == "paid"
    # Lowered / removed later: still nothing due, nothing on the wallet.
    await svc.set_tip(str(done["_id"]), 0, s["mgr"]["id"], "manager", s["center_id"])
    d = await fb.doc(db, str(done["_id"]))
    assert d["total_amount"] == pytest.approx(done["total_amount"]) and d["amount_paid"] == pytest.approx(done["amount_paid"])
    assert bm.amount_due(d) == 0 and bm.money_view(d)["payment_status"] == "paid"
    assert await fb.balance(db, s["cu"]["id"]) == pytest.approx(balance)


async def test_tip_on_a_booking_part_paid_by_wallet_creates_no_due(db):
    s = await fb.rig(db)
    done = await _prepaid_marked_done(db, s, wallet_credit=100)
    assert done["wallet_applied"] == pytest.approx(100)
    await BookingService(db).set_tip(str(done["_id"]), 30, s["mgr"]["id"], "manager", s["center_id"])
    d = await fb.doc(db, str(done["_id"]))
    from app.services import booking_money as bm

    assert bm.amount_due(d) == 0 and bm.money_view(d)["payment_status"] == "paid"
    assert d["wallet_applied"] == pytest.approx(100) and await fb.balance(db, s["cu"]["id"]) == pytest.approx(0)


async def test_tip_is_refused_on_a_booking_the_manager_did_not_do(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.pay_online(db, b["id"])
    with pytest.raises(BadRequestException, match="done by the manager"):
        await BookingService(db).set_tip(b["id"], 50, s["mgr"]["id"], "manager", s["center_id"])
    d = await fb.doc(db, b["id"])
    assert d["total_amount"] == b["total_amount"] and not d.get("tip_amount")


async def test_logged_discount_never_meets_the_customer_wallet(db):
    """The only manager discount: recorded on a logged job after the bill,
    with the wallet left alone — credit isn't spent on it, so the discounted
    total can never end up below what was taken (no overpayment), and a
    debt isn't silently carried into it."""
    s = await fb.rig(db)
    await fb.credit(db, s["cu"]["id"], 300)
    hatch, star = await fb.get_hatchback_type_id(db), await fb.get_star_wash_service_id(db)
    result = await BookingService(db).create_manager_logged_visit(
        ManagerLogBookingRequest(
            customer_name="Wallet Walt", customer_phone=await _phone(db, s["cu"]["id"]),
            lines=[QuickBookingLine(vehicle_type=hatch, quantity=1, service_ids=[star])],
            scheduled_date=h.day(-1), service_time="10:30", address_line="Logged Lane 9, Vijay Nagar",
            send_whatsapp=False, discount_amount=100,
        ),
        manager_id=s["mgr"]["id"], manager_center_id=s["center_id"],
    )
    raw = await fb.doc(db, result["bookings"][0]["id"])
    assert raw["manager_discount"] == 100
    assert raw["amount_paid"] == pytest.approx(raw["total_amount"]) and raw["amount_due"] == 0
    assert not raw.get("wallet_applied") and raw["payment_status"] == "paid"
    assert await fb.balance(db, s["cu"]["id"]) == pytest.approx(300)


def test_no_manager_discount_path_exists_on_an_existing_booking():
    """Guard: the manager-discount case on a NORMAL (possibly prepaid)
    booking is unreachable today — only Log A Done Job takes one. A future
    discount on an existing booking must re-price through
    MoneyService.on_price_change / after_price_change (overpaid → wallet,
    open links voided); this test fails the day such a field appears so
    that wiring isn't forgotten."""
    from app.schemas.booking_schema import BookingUpdateDetailsRequest

    for schema in (ManagerMarkDoneRequest, BookingUpdateDetailsRequest):
        assert not any("discount" in f or "price" in f or "total" in f for f in schema.model_fields), schema
