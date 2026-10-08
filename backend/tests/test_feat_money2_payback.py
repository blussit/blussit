"""MONEY-2 (founder 2026-10-07): a manager never adds money to a customer
wallet at will — he only pays money BACK for one of that customer's
bookings that went wrong (cancelled, delayed, complaint, service issue).

POST /customers/{id}/wallet/payout now needs the booking, a reason, the
method and (unless cash) the reference. Credit in the wallet is debited;
with no credit it is a goodwill payback recorded on the booking
(`manager_paybacks[]`). Per booking, everything paid back never exceeds
what the customer paid for it. Local replica-set Mongo only."""
import asyncio

import pytest
from bson import ObjectId

from app.core.exceptions import AppException, BadRequestException, NotFoundException
from app.services import booking_money as bm
from app.services.booking_service import BookingService
from app.services.customer_wallet_service import CustomerWalletService, in_transaction
from app.services.money_service import MoneyService
from app.services.payment_service import PaymentService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio


def _actor(s: dict) -> dict:
    return {"id": s["mgr"]["id"], "role": "manager"}


async def _cancelled_paid(db, s: dict, slot: int = 0) -> tuple[dict, float]:
    """A booking paid online, then cancelled in time: the whole amount is
    now credit in the customer's wallet. Returns (booking doc, paid)."""
    b = await fb.book(db, s["cu"], s["when"], s["keys"][slot])
    await fb.pay_online(db, b["id"])
    paid = bm.amount_paid_of(await fb.doc(db, b["id"]))
    await BookingService(db).cancel_booking(b["id"], fb.cancel(), s["cu"]["id"], "customer")
    return await fb.doc(db, b["id"]), paid


async def _done(db, s: dict, slot: int = 0, **signals) -> tuple[dict, float]:
    """A completed booking, paid in full online, with the given signal
    fields (delay_minutes, issue_flag, …). Returns (booking doc, paid)."""
    b = await fb.book(db, s["cu"], s["when"], s["keys"][slot])
    await fb.pay_online(db, b["id"])
    await db.bookings.update_one({"_id": ObjectId(b["id"])}, {"$set": {"status": "completed", **signals}})
    doc = await fb.doc(db, b["id"])
    return doc, bm.amount_paid_of(doc)


def _body(booking_id: str, **kw) -> dict:
    body = {"booking_id": booking_id, "reason": "delayed", "method": "upi", "reference": "UTR-MONEY2-1", "amount": 100}
    body.update(kw)
    return body


async def _payback(db, s: dict, customer_id: str, booking_id: str, **kw):
    actor = kw.pop("actor", None) or _actor(s)
    center = kw.pop("center", s["center_id"])
    args = _body(booking_id, **kw)
    return await CustomerWalletService(db).payback(
        customer_id, booking_id=args["booking_id"], amount=args["amount"], reason=args["reason"], method=args["method"],
        reference=args.get("reference"), note=args.get("note"), idempotency_key=args.get("idempotency_key"),
        actor=actor, center=center,
    )


# ------------------------------------------------------------- eligibility


async def test_plain_completed_booking_is_refused(db):
    s = await fb.rig(db)
    doc, _ = await _done(db, s)
    with pytest.raises(BadRequestException) as err:
        await _payback(db, s, s["cu"]["id"], str(doc["_id"]))
    assert err.value.error_code == "PAYBACK_NOT_ALLOWED"
    assert "only for a booking that was cancelled, delayed or had an issue" in str(err.value.message)
    async with h.client() as c:
        r = await c.post(f"/api/v1/customers/{s['cu']['id']}/wallet/payout", json=_body(str(doc["_id"])), headers=s["mgr"]["h"])
    assert r.status_code == 400 and r.json()["error_code"] == "PAYBACK_NOT_ALLOWED", r.text
    assert (await fb.doc(db, str(doc["_id"]))).get("manager_paybacks") in (None, [])


async def test_signals_that_make_a_booking_eligible(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    delayed, _ = await _done(db, s, 0, delay_minutes=35)
    flagged, _ = await _done(db, s, 1, issue_flag="service_overrun", issue_resolved=True)
    late, _ = await _done(db, s, 2, late_penalty_amount=20)
    complained, _ = await _done(db, s, 3)
    await db.complaints.insert_one({"booking_id": str(complained["_id"]), "customer_id": cid, "service_center_id": s["center_id"],
                                    "subject": "Car still dirty", "status": "open"})
    for doc, reason in ((delayed, "delayed"), (flagged, "service_issue"), (late, "delayed"), (complained, "complaint")):
        out = await _payback(db, s, cid, str(doc["_id"]), reason=reason, method="cash", reference=None,
                             idempotency_key=f"k-{doc['_id']}", amount=10)
        assert out["created"] and out["goodwill_amount"] == 10, (reason, out)


async def test_reason_must_match_and_other_needs_a_note(db):
    s = await fb.rig(db)
    doc, _ = await _done(db, s, delay_minutes=20)
    with pytest.raises(BadRequestException) as err:   # not cancelled
        await _payback(db, s, s["cu"]["id"], str(doc["_id"]), reason="cancelled")
    assert err.value.error_code == "PAYBACK_NOT_ALLOWED"
    with pytest.raises(BadRequestException):            # no complaint on it
        await _payback(db, s, s["cu"]["id"], str(doc["_id"]), reason="complaint")
    with pytest.raises(BadRequestException):
        await _payback(db, s, s["cu"]["id"], str(doc["_id"]), reason="other", note="short")
    out = await _payback(db, s, s["cu"]["id"], str(doc["_id"]), reason="other", note="Customer waited at the gate")
    assert out["created"]


# ------------------------------------------------------------- wallet mode


async def test_cancelled_booking_pays_the_wallet_credit_back(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _cancelled_paid(db, s)
    assert await fb.balance(db, cid) == paid > 0
    out = await _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=paid, reference="UTR-CANCEL-1")
    assert out["created"] and out["wallet_amount"] == paid and out["goodwill_amount"] == 0
    assert await fb.balance(db, cid) == 0
    row = await db.customer_wallet_ledger.find_one({"customer_id": cid, "kind": "payout"})
    assert row["booking_id"] == str(doc["_id"]) and row["booking_number"] == doc["booking_number"]
    assert row["note"].startswith("Paid By Manager") and row["amount"] == -paid
    assert row["meta"]["reason"] == "cancelled" and row["meta"]["method"] == "upi"
    after = await fb.doc(db, str(doc["_id"]))
    assert after["paid_back_total"] == paid and after["manager_paybacks"][0]["wallet_amount"] == paid
    assert await db.audit_logs.count_documents({"action": "CUSTOMER_PAYBACK", "target_id": cid}) == 1
    # A retry with the same reference records nothing new.
    again = await _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=paid, reference="UTR-CANCEL-1")
    assert again["created"] is False and await fb.balance(db, cid) == 0
    # Nothing more can go back for it.
    with pytest.raises(BadRequestException):
        await _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=1, reference="UTR-CANCEL-2")


async def test_cancelled_booking_never_gets_goodwill_on_top_of_its_refund(db):
    """Its money already went back to the wallet: once that credit is spent,
    paying cash for it again would refund it twice."""
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _cancelled_paid(db, s)
    await CustomerWalletService(db).post(cid, -paid, "adjustment", key=f"spent:{cid}")
    with pytest.raises(BadRequestException) as err:
        await _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=50, reference="UTR-X-1")
    assert err.value.error_code == "WALLET_BALANCE_TOO_LOW"


async def test_wallet_cap_is_what_was_paid_for_that_booking(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _cancelled_paid(db, s)
    await fb.credit(db, cid, 1000)   # unrelated credit
    with pytest.raises(BadRequestException) as err:
        await _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=paid + 1, reference="UTR-CAP-1")
    assert err.value.error_code == "PAYBACK_TOO_MUCH"
    assert await fb.balance(db, cid) == paid + 1000


# ------------------------------------------------------------- goodwill mode


async def test_goodwill_payback_on_a_delayed_job(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _done(db, s, delay_minutes=45)
    assert await fb.balance(db, cid) == 0
    first = await _payback(db, s, cid, str(doc["_id"]), method="cash", reference=None, amount=100, idempotency_key="gw-1")
    assert first["created"] and first["goodwill_amount"] == 100 and first["wallet_amount"] == 0
    assert await fb.balance(db, cid) == 0                        # no wallet credit / debit
    assert await db.customer_wallet_ledger.count_documents({"customer_id": cid}) == 0
    dup = await _payback(db, s, cid, str(doc["_id"]), method="cash", reference=None, amount=100, idempotency_key="gw-1")
    assert dup["created"] is False
    with pytest.raises(BadRequestException) as err:
        await _payback(db, s, cid, str(doc["_id"]), method="cash", reference=None, amount=paid, idempotency_key="gw-2")
    assert err.value.error_code == "PAYBACK_TOO_MUCH"
    rest = await _payback(db, s, cid, str(doc["_id"]), method="upi", reference="UTR-GW-3", amount=bm.r2(paid - 100))
    assert rest["created"]
    after = await fb.doc(db, str(doc["_id"]))
    assert after["paid_back_total"] == paid and len(after["manager_paybacks"]) == 2
    entry = after["manager_paybacks"][0]
    assert {"amount", "method", "reference", "reason", "by", "at"} <= set(entry)
    # Cash without a key: the same amount in the same minute is one payback.
    other, _ = await _done(db, s, 1, delay_minutes=30)
    a = await _payback(db, s, cid, str(other["_id"]), method="cash", reference=None, amount=40)
    b = await _payback(db, s, cid, str(other["_id"]), method="cash", reference=None, amount=40)
    assert a["created"] and b["created"] is False


async def test_reference_required_unless_cash(db):
    s = await fb.rig(db)
    doc, _ = await _done(db, s, delay_minutes=45)
    with pytest.raises(BadRequestException):
        await _payback(db, s, s["cu"]["id"], str(doc["_id"]), method="upi", reference=None)
    async with h.client() as c:
        r = await c.post(f"/api/v1/customers/{s['cu']['id']}/wallet/payout",
                         json=_body(str(doc["_id"]), method="bank_transfer", reference=""), headers=s["mgr"]["h"])
        assert r.status_code in (400, 422), r.text


# ------------------------------------------------------------- scope


async def test_scope_negatives_over_http(db):
    a = await fb.rig(db)
    b = await fb.rig(db)
    admin = await h.admin(db)
    doc, paid = await _done(db, a, delay_minutes=50)
    other_doc, _ = await _done(db, b, delay_minutes=50)
    cu = a["cu"]["id"]
    url = f"/api/v1/customers/{cu}/wallet/payout"
    async with h.client() as c:
        # The old free payout (no booking) is gone.
        r = await c.post(url, json={"amount": 10, "method": "upi", "reference": "UTR123456"}, headers=a["mgr"]["h"])
        assert r.status_code == 422, r.text
        assert (await c.post(url, json=_body(str(doc["_id"])), headers=b["mgr"]["h"])).status_code == 404
        assert (await c.post(url, json=_body(str(doc["_id"])), headers=a["cu"]["h"])).status_code == 403
        assert (await c.post(url, json=_body(str(doc["_id"])), headers=a["cap"]["h"])).status_code == 403
        # Another customer's booking, through this customer's URL.
        r = await c.post(url, json=_body(str(other_doc["_id"])), headers=a["mgr"]["h"])
        assert r.status_code == 404, r.text
        r = await c.post(url, json=_body(str(other_doc["_id"])), headers=admin["h"])
        assert r.status_code == 404, r.text
        r = await c.post(url, json=_body(str(ObjectId())), headers=a["mgr"]["h"])
        assert r.status_code == 404, r.text
        for bad in (0, -5, "NaN", 1e12):
            r = await c.post(url, json=_body(str(doc["_id"]), amount=bad), headers=a["mgr"]["h"])
            assert r.status_code == 422, (bad, r.text)
        r = await c.post(url, json=_body(str(doc["_id"]), reason="refund"), headers=a["mgr"]["h"])
        assert r.status_code == 422, r.text
        r = await c.post(url, json=_body(str(doc["_id"]), amount=paid + 1), headers=a["mgr"]["h"])
        assert r.status_code == 400 and r.json()["error_code"] == "PAYBACK_TOO_MUCH", r.text
        ok = await c.post(url, json=_body(str(doc["_id"])), headers=a["mgr"]["h"])
        assert ok.status_code == 200 and ok.json()["data"]["created"] is True, ok.text
        dup = await c.post(url, json=_body(str(doc["_id"])), headers=a["mgr"]["h"])
        assert dup.status_code == 200 and dup.json()["data"]["created"] is False
        # Admin: any center.
        r = await c.post(f"/api/v1/customers/{b['cu']['id']}/wallet/payout",
                         json=_body(str(other_doc["_id"]), reference="UTR-ADMIN-1"), headers=admin["h"])
        assert r.status_code == 200, r.text
        # The admin's free adjust stays admin-only.
        r = await c.post(f"/api/v1/customers/{cu}/wallet/adjust", json={"amount": 5, "note": "goodwill"}, headers=a["mgr"]["h"])
        assert r.status_code == 403


# ------------------------------------------------------------- concurrency


async def test_two_goodwill_paybacks_racing_never_exceed_what_was_paid(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _done(db, s, delay_minutes=60)
    share = bm.r2(paid * 0.6)
    outcome = await asyncio.gather(
        _payback(db, s, cid, str(doc["_id"]), amount=share, reference="UTR-RACE-A"),
        _payback(db, s, cid, str(doc["_id"]), amount=share, reference="UTR-RACE-B"),
        return_exceptions=True,
    )
    ok = [o for o in outcome if not isinstance(o, Exception)]
    assert len(ok) == 1, outcome
    assert all(isinstance(o, AppException) for o in outcome if isinstance(o, Exception)), outcome
    after = await fb.doc(db, str(doc["_id"]))
    assert after["paid_back_total"] == share and len(after["manager_paybacks"]) == 1
    assert await db.customer_paybacks.count_documents({"booking_id": str(doc["_id"])}) == 1


async def test_two_wallet_paybacks_racing_on_one_credit(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _cancelled_paid(db, s)
    share = bm.r2(paid * 0.6)
    outcome = await asyncio.gather(
        _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=share, reference="UTR-W-A"),
        _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=share, reference="UTR-W-B"),
        return_exceptions=True,
    )
    assert sum(1 for o in outcome if not isinstance(o, Exception)) == 1, outcome
    assert await fb.balance(db, cid) == bm.r2(paid - share)
    assert await db.customer_wallet_ledger.count_documents({"customer_id": cid, "kind": "payout"}) == 1


async def test_payback_vs_a_booking_spending_the_same_credit(db):
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, paid = await _cancelled_paid(db, s)
    money = MoneyService(db)
    draft = {"_id": ObjectId(), "customer_id": cid, "booking_number": "T-M2-RACE", "total_amount": 300,
             "platform_earning": 300, "payment_status": "pending", "status": "pending"}

    async def _create():
        async def _do(session):
            out = await money.apply_wallet_at_create(cid, [dict(draft)], session)
            await db.bookings.insert_one(out[0], session=session)
            return out

        return await in_transaction(db, _do)

    outcome = await asyncio.gather(
        _payback(db, s, cid, str(doc["_id"]), reason="cancelled", amount=paid, reference="UTR-VS-1"), _create(),
        return_exceptions=True,
    )
    assert all(isinstance(o, AppException) for o in outcome if isinstance(o, Exception)), outcome
    balance = await fb.balance(db, cid)
    assert balance >= 0
    paid_out = 0 if isinstance(outcome[0], Exception) else outcome[0]["amount"]
    spent = 0 if isinstance(outcome[1], Exception) else outcome[1][0]["wallet_applied"]
    assert paid_out + spent + balance == paid, (outcome, balance)
    assert paid_out in (0, paid)
    await db.bookings.delete_many({"booking_number": "T-M2-RACE"})


# ------------------------------------------------------------- admin list + reports


async def test_admin_payouts_list_and_collections_line(db):
    a = await fb.rig(db)
    b = await fb.rig(db)
    admin = await h.admin(db)
    gdoc, _ = await _done(db, a, 1, delay_minutes=40)   # booked first: the credit below stays unspent
    wdoc, wpaid = await _cancelled_paid(db, a)
    bdoc, _ = await _done(db, b, delay_minutes=40)
    await _payback(db, a, a["cu"]["id"], str(wdoc["_id"]), reason="cancelled", amount=wpaid, reference="UTR-L-1")
    await _payback(db, a, a["cu"]["id"], str(gdoc["_id"]), method="cash", reference=None, amount=70, idempotency_key="l-2")
    await _payback(db, b, b["cu"]["id"], str(bdoc["_id"]), amount=30, reference="UTR-L-3")
    async with h.client() as c:
        assert (await c.get("/api/v1/wallet/payouts", headers=a["mgr"]["h"])).status_code == 403
        r = await c.get(f"/api/v1/wallet/payouts?service_center_id={a['center_id']}", headers=admin["h"])
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        assert {x["booking_number"] for x in rows} == {wdoc["booking_number"], gdoc["booking_number"]}
        for x in rows:
            assert x["service_center_id"] == a["center_id"] and x["center_name"]
            assert x["reason"] and x["method"] and x["paid_by_name"] is not None and x["paid_by_role"] == "manager"
            assert x["source"] == "manager_payback" and x["mode"] in ("wallet", "goodwill")
        modes = {x["mode"] for x in rows}
        assert modes == {"wallet", "goodwill"}
        today = h.day(0)
        r = await c.get(f"/api/v1/wallet/payouts?date_from={today}&date_to={today}", headers=admin["h"])
        assert r.status_code == 200 and len([x for x in r.json()["data"] if x["service_center_id"] in (a["center_id"], b["center_id"])]) == 3
        r = await c.get(f"/api/v1/wallet/payouts?date_from={h.day(-5)}&date_to={h.day(-4)}", headers=admin["h"])
        assert not [x for x in r.json()["data"] if x["service_center_id"] in (a["center_id"], b["center_id"])]
    ps = PaymentService(db)
    center = await ps.center_collections(a["center_id"], "manager", a["center_id"], h.day(-3), h.day(3))
    assert center["totals"]["paid_back_by_managers"] == bm.r2(wpaid + 70)
    assert center["paid_back_by_managers"]["amount"] == bm.r2(wpaid + 70)
    assert center["totals"]["net_collected"] == bm.r2(center["totals"]["cash_amount"] + center["totals"]["online_amount"] - wpaid - 70)
    rep = await ps.admin_collections(h.day(-3), h.day(3))
    row_a = next(r for r in rep["rows"] if r["service_center_id"] == a["center_id"])
    row_b = next(r for r in rep["rows"] if r["service_center_id"] == b["center_id"])
    assert row_a["paid_back_by_managers"] == bm.r2(wpaid + 70) and row_b["paid_back_by_managers"] == 30
    assert rep["totals"]["paid_back_by_managers"] >= bm.r2(wpaid + 100)


async def test_no_manager_path_credits_a_wallet_without_a_reason(db):
    """The only staff wallet write that isn't tied to a booking/charge is the
    admin adjust; the payback route only ever debits."""
    s = await fb.rig(db)
    cid = s["cu"]["id"]
    doc, _ = await _done(db, s, delay_minutes=30)
    await _payback(db, s, cid, str(doc["_id"]), amount=50, reference="UTR-NC-1")
    rows = await db.customer_wallet_ledger.find({"customer_id": cid, "actor_role": "manager", "amount": {"$gt": 0}}).to_list(None)
    assert rows == []
    with pytest.raises(NotFoundException):
        await CustomerWalletService(db).adjust(cid, 100, "free money", _actor(s))
