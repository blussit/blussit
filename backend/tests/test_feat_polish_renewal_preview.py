"""FINAL-POLISH (2026-10-08): the preview of a custom-plan RENEWAL.

`POST /subscriptions/custom-plans/preview` used to refuse the cars being
renewed ("already has an active plan") — they are still on the passes the
renewal renews. With `renewal_of` (the paid cart being renewed, or a draft
renewal being revised) the preview applies exactly the live-pass rule
`/renew` and revise apply: a car's own renewed pass lets it through; any
other live pass still refuses it. Razorpay is stubbed; local Mongo only."""
import pytest

from app.core.exceptions import BadRequestException
from app.schemas.custom_plan_schema import CustomPlanPreviewRequest, CustomPlanRenewRequest, CustomPlanReviseRequest
from app.services.custom_plan_service import CustomPlanService

from tests.plans2_factories import (  # noqa: F401 — fixtures
    _tidy,
    activated_cash,
    gateway,
    make_cart,
    mgr,
    rig,
)
from tests.society_factories import auth, client

pytestmark = pytest.mark.asyncio


def _cars_of(view: dict) -> list[dict]:
    return [
        {"vehicle_id": c["vehicle_id"], "items": [{"service_id": i["service_id"], "count": int(i["count"])} for i in c["items"]]}
        for c in view["cars"]
    ]


def _role(rig) -> dict:
    return {"actor_role": "manager", "actor_center_id": rig["center"]}


async def test_renewal_preview_prices_like_renew_and_lets_the_renewed_cars_through(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    cars = _cars_of(old)
    # Without renewal_of the cars are still on their live passes: refused.
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await service.preview(CustomPlanPreviewRequest(customer_id=rig["customer"], cars=cars), **_role(rig))
    # With it: priced, and exactly what /renew then charges.
    preview = await service.preview(CustomPlanPreviewRequest(renewal_of=old["id"], cars=cars, discount_amount=20), **_role(rig))
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(discount_amount=20), **mgr(rig))
    assert (preview["subtotal"], preview["discount_amount"], preview["total_amount"]) == (
        renewal["subtotal"], renewal["discount_amount"], renewal["total_amount"],
    )
    assert [c["amount"] for c in preview["cars"]] == [c["amount"] for c in renewal["cars"]]
    # Revising the DRAFT renewal: its preview (renewal_of = the draft) works
    # too and matches what revise saves.
    fewer = [{**cars[0], "items": cars[0]["items"][:1]}, cars[1]]
    draft_preview = await service.preview(CustomPlanPreviewRequest(renewal_of=renewal["id"], cars=fewer), **_role(rig))
    revised = await service.revise(renewal["id"], CustomPlanReviseRequest(expected_revision=1, cars=fewer), **mgr(rig))
    assert draft_preview["total_amount"] == revised["total_amount"] < renewal["total_amount"] + 20


async def test_renewal_preview_still_refuses_a_car_on_another_live_pass(db, rig, gateway):
    old = await activated_cash(db, rig)
    service = CustomPlanService(db)
    other_car = {"registration_number": rig["plate_c"], "vehicle_type": rig["hatch"],
                 "items": [{"service_id": rig["svc"]["star-wash"], "count": 2}]}
    await activated_cash(db, rig, car_list=[other_car])   # plate C: its own live plan
    cars = [*_cars_of(old), other_car]
    with pytest.raises(BadRequestException, match="already has an active plan") as err:
        await service.preview(CustomPlanPreviewRequest(renewal_of=old["id"], cars=cars), **_role(rig))
    assert rig["plate_c"] in err.value.message and "Last Booking Day:" in err.value.message
    # /renew refuses the same list with the same words.
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await service.renew(old["id"], CustomPlanRenewRequest(cars=cars), **mgr(rig))
    # And a draft renewal's preview refuses it too.
    renewal = await service.renew(old["id"], CustomPlanRenewRequest(), **mgr(rig))
    with pytest.raises(BadRequestException, match="already has an active plan"):
        await service.preview(CustomPlanPreviewRequest(renewal_of=renewal["id"], cars=cars), **_role(rig))


async def test_renewal_preview_scope_and_cart_rules(db, rig, gateway):
    old = await activated_cash(db, rig)
    cars = _cars_of(old)
    async with client() as http:
        url = "/api/v1/subscriptions/custom-plans/preview"
        ok = await http.post(url, json={"renewal_of": old["id"], "cars": cars}, headers=auth(rig["manager"], "manager", rig["center"]))
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["total_amount"] > 0
        # Admin may preview it too.
        admin = await http.post(url, json={"renewal_of": old["id"], "cars": cars}, headers=auth(rig["admin"], "admin"))
        assert admin.status_code == 200, admin.text
        # Another center's manager can't reach the cart.
        other = await http.post(url, json={"renewal_of": old["id"], "cars": cars}, headers=auth(rig["other_manager"], "manager", rig["other"]))
        assert other.status_code in (403, 404), other.text
    service = CustomPlanService(db)
    # An unpaid cart that isn't a renewal can't be "renewed" in a preview.
    draft = await make_cart(db, rig, car_list=[{"registration_number": rig["plate_c"], "vehicle_type": rig["hatch"],
                                                "items": [{"service_id": rig["svc"]["star-wash"], "count": 1}]}])
    with pytest.raises(BadRequestException, match="Only a paid custom plan"):
        await service.preview(CustomPlanPreviewRequest(renewal_of=draft["id"], cars=cars), **_role(rig))
    # The renewal is for the plan's own customer.
    with pytest.raises(BadRequestException, match="own customer"):
        await service.preview(CustomPlanPreviewRequest(renewal_of=old["id"], customer_id=rig["manager"], cars=cars), **_role(rig))
