"""
Society plans paid by WhatsApp link (founder, 2026-10-06): the manager sends
the resident a Razorpay payment link; paying it activates (or renews) the
plan by itself. Cash stays available — marking it paid in cash cancels the
link so nobody pays twice. Razorpay is stubbed (no external call).
"""
import itertools

import pytest
from bson import ObjectId

from app.core.exceptions import ForbiddenException
from app.services import payment_service
from app.services.payment_service import PaymentService
from app.services.society_service import SocietyService

from tests.society_factories import activate_cash, enroll, make_center, make_resident, make_society, make_template, staff

pytestmark = pytest.mark.asyncio

_seq = itertools.count(1)


class _StubLinks:
    cancelled: list[str] = []

    def create(self, payload):
        n = next(_seq)
        return {"id": f"plink_soc_{n:06d}", "short_url": f"https://rzp.io/l/soc{n}", **payload}

    def cancel(self, link_id):
        self.cancelled.append(link_id)
        return {"id": link_id, "status": "cancelled"}


class _StubClient:
    payment_link = _StubLinks()


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "rzp_test_stub")
    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_SECRET", "stub_secret_key")
    monkeypatch.setattr(payment_service, "_razorpay_client", lambda: _StubClient())


@pytest.fixture
async def rig(db, cleanup):
    center = await make_center(db, cleanup, spot=0)
    society = await make_society(db, cleanup, center)
    plan = await make_template(db, cleanup)
    manager_id, _ = await staff(db, cleanup, center["id"])
    cleanup.append(("payment_orders", {"purpose": "society", "kind": "link"}))
    return {"center": center, "society": society, "plan": plan, "manager_id": manager_id}


async def test_link_payment_activates_the_plan_and_is_reused_not_duplicated(db, cleanup, rig, gateway):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=2)
    pay = PaymentService(db)
    kw = dict(renewal=False, actor_id=rig["manager_id"], actor_role="manager", actor_center_id=rig["center"]["id"])

    first = await pay.create_society_link(view["id"], **kw)
    assert first["amount"] == 2 * 1649 and first["short_url"].startswith("https://rzp.io/") and not first["reused"]
    again = await pay.create_society_link(view["id"], **kw)
    assert again["reused"] and again["short_url"] == first["short_url"]
    assert await db.payment_orders.count_documents({"kind": "link", "purpose": "society", "society_enrollment_id": view["id"]}) == 1
    enrollment = await db.society_enrollments.find_one({"_id": ObjectId(view["id"])})
    assert enrollment["status"] == "awaiting_payment"
    # The resident got it (in-app + WhatsApp through notify()).
    assert await db.notifications.count_documents({"user_id": str(resident["_id"]), "message": {"$regex": first["short_url"]}}) >= 1

    # Another center's manager can't send one.
    with pytest.raises(ForbiddenException):
        await pay.create_society_link(view["id"], renewal=False, actor_id=rig["manager_id"], actor_role="manager", actor_center_id=str(ObjectId()))

    # The resident pays the link -> active, passes created, paid online.
    order = await db.payment_orders.find_one({"kind": "link", "society_enrollment_id": view["id"]})
    result = await pay._apply_link_paid(order["razorpay_link_id"], "pay_link_soc_1")
    assert result["status"] == "paid" and result["settled"]
    enrollment = await db.society_enrollments.find_one({"_id": ObjectId(view["id"])})
    assert enrollment["status"] == "active" and enrollment["payment"]["method"] == "online"
    assert await db.user_subscriptions.count_documents({"enrollment_id": view["id"], "payment_method": "online"}) == 2
    # Settling twice (callback + sweep) changes nothing.
    await pay._apply_link_paid(order["razorpay_link_id"], "pay_link_soc_1")
    assert await db.user_subscriptions.count_documents({"enrollment_id": view["id"]}) == 2


async def test_cash_after_a_link_cancels_the_link(db, cleanup, rig, gateway):
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    pay = PaymentService(db)
    link = await pay.create_society_link(view["id"], renewal=False, actor_id=rig["manager_id"], actor_role="manager", actor_center_id=rig["center"]["id"])
    await activate_cash(db, view["id"], actor_id=rig["manager_id"])
    assert await pay.void_society_links(view["id"], "paid in cash", renewal=False) == 1
    order = await db.payment_orders.find_one({"short_url": link["short_url"]})
    assert order["status"] == "voided" and order["razorpay_link_id"] in _StubLinks.cancelled
    # And the sweep treats any leftover link for a paid plan as moot.
    assert await pay._link_is_moot({**order, "status": "created"}) is True
    enrollment = await SocietyService(db).get_enrollment(view["id"])
    assert enrollment["status"] == "active"


async def test_no_online_payment_means_the_request_is_left_untouched(db, cleanup, rig, monkeypatch):
    from app.core.exceptions import BadRequestException

    monkeypatch.setattr(payment_service.settings, "RAZORPAY_KEY_ID", "")
    resident = await make_resident(db, cleanup)
    view = await enroll(db, rig["society"], resident, rig["plan"]["id"], cars=1)
    with pytest.raises(BadRequestException):
        await PaymentService(db).create_society_link(view["id"], renewal=False, actor_id=rig["manager_id"], actor_role="manager", actor_center_id=rig["center"]["id"])
    enrollment = await db.society_enrollments.find_one({"_id": ObjectId(view["id"])})
    assert enrollment["status"] == "requested"
