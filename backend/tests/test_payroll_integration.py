"""Integration seams between payroll, franchise, refunds and finance (§7.13):
a refund reverses commission, the franchise pay-finance rule is one rule, the
Finance source filter groups by family and a locked entry names where to change it."""
import pytest
from bson import ObjectId

from app.services import booking_money as bm
from app.services.finance_service import FinanceService, locked_message
from app.services.franchise_service import captain_pay_finance_impact
from app.services.payment_service import PaymentService
from tests import test_fix_core_helpers as h
from tests.payroll_helpers import complete_job, dues, make_payroll, rig

pytestmark = pytest.mark.asyncio


async def test_a_refund_due_reverses_the_commission_and_clearing_it_restores(db):
    s = await rig(db)
    async with h.client() as c:
        await make_payroll(c, s, "commission", commission={"mode": "percent", "percent": 40})
        b = await complete_job(db, s)
        earned = await dues(db, s["cap"]["id"])
        assert earned > 0
        await db.bookings.update_one({"_id": b["_id"]}, {"$set": {"payment_status": "paid"}})
        pay = PaymentService(db)
        await pay._mark_refund_due(str(b["_id"]))
        assert await dues(db, s["cap"]["id"]) == 0
        await pay.clear_refund_due([str(b["_id"])], "refund cancelled")
        assert await dues(db, s["cap"]["id"]) == earned
        # the same refund twice posts nothing extra
        await pay._mark_refund_due(str(b["_id"]))
        await pay._mark_refund_due(str(b["_id"]))
        assert await dues(db, s["cap"]["id"]) == 0


def test_franchise_pay_finance_rule_is_one_rule():
    franchise_paid = {"operation": {"type": "franchise", "captain_pay_managed_by": "franchise"}}
    franchise_blussit = {"operation": {"type": "franchise", "captain_pay_managed_by": "blussit"}}
    self_op = {"operation": {"type": "self_operated"}}
    assert captain_pay_finance_impact(franchise_paid) is False
    assert captain_pay_finance_impact(franchise_blussit) is True
    assert captain_pay_finance_impact(self_op) is True
    assert captain_pay_finance_impact(None) is True


def test_finance_source_filter_groups_a_family_but_matches_exact_names():
    f = FinanceService.__new__(FinanceService)
    assert f._filters(source="franchise")["source"] == {"$regex": "^franchise"}
    assert f._filters(source="payroll")["source"] == {"$regex": "^payroll"}
    assert f._filters(source="franchise_fee")["source"] == "franchise_fee"


def test_locked_entry_message_names_the_right_place():
    assert "Franchise" in locked_message({"source": "franchise_settlement"})
    assert "Franchise" in locked_message({"source": "franchise_kit_sale"})
    assert "deposit" in locked_message({"source": "payroll_deposit"}).lower()
    assert locked_message({"source": "payroll"}).startswith("Created by Salary")
