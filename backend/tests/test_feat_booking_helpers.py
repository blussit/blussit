"""Shared helpers for the test_feat_booking_* suites (feature build
2026-10-07: customer cancel/edit, on-site add-ons, wallet at create,
completion & collection, reminders). Builds on the round-1/2 audit helpers.
No tests live here."""
import itertools
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest
from app.services.booking_service import BookingService
from app.services.customer_wallet_service import CustomerWalletService
from app.services.money_service import MoneyService
from tests import test_fix_core_helpers as h
from tests import test_fix_coreb_helpers as hb
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, get_suv_type_id

_KEY = itertools.count(1)


async def rig(db, **center_kw) -> dict:
    """A center with its named manager, a captain (₹500 wallet) and a
    customer with a saved address there."""
    return await hb.staffed_center(db) if not center_kw else await _rig_kw(db, **center_kw)


async def _rig_kw(db, **kw) -> dict:
    cid, pin = await h.center(db, **kw)
    when, keys = await h.slot_keys(db, cid)
    mgr = await h.manager(db, cid)
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"manager_id": mgr["id"]}})
    return {"center_id": cid, "pin": pin, "when": when, "keys": keys, "cu": await h.customer(db, pin), "mgr": mgr,
            "cap": await h.captain(db, cid, wallet=500.0)}


async def book(db, cu: dict, when: str, slot: str, **kw) -> dict:
    """One car through the customer's own create path (wallet applied)."""
    return await hb.new_booking(db, cu, when, slot, **kw)


async def slot_in(db, ids, minutes: float) -> None:
    await hb.slot_in(db, ids, minutes)


def cancel(reason: str = "Plans changed", **kw) -> BookingCancelRequest:
    return BookingCancelRequest(reason=reason, **kw)


async def doc(db, booking_id: str) -> dict:
    return await db.bookings.find_one({"_id": ObjectId(booking_id)})


async def balance(db, customer_id: str) -> float:
    return await CustomerWalletService(db).balance(customer_id)


async def credit(db, customer_id: str, amount: float) -> None:
    """Give the customer wallet credit (+) or a debt (−) — an admin adjustment row."""
    await CustomerWalletService(db).post(customer_id, amount, "adjustment", key=f"test_adj:{customer_id}:{next(_KEY)}", note="test")


async def pay_online(db, booking_ids: list[str] | str, amount: float | None = None) -> dict:
    """Money received online for these cars, through the one real path
    (MoneyService.apply_payment) — what verify/webhook/sweep all call."""
    ids = [booking_ids] if isinstance(booking_ids, str) else list(booking_ids)
    if amount is None:
        from app.services import booking_money as bm

        amount = sum([bm.amount_due(await doc(db, i)) for i in ids])
    return await MoneyService(db).apply_payment(ids, amount, method="online", key=f"test_pay:{next(_KEY)}")


async def pass_for(db, customer_id: str, washes: int = 4) -> str:
    from app.schemas.subscription_schema import SubscribeRequest
    from app.services.subscription_service import UserSubscriptionService
    from tests.factories import make_subscription_plan

    star = await get_star_wash_service_id(db)
    plan_id = await make_subscription_plan(db, vehicle_types=[], included_service_ids=[star], total_service_count=washes)
    sub = await UserSubscriptionService(db).subscribe(
        customer_id, SubscribeRequest(plan_id=plan_id, vehicle_type=await get_hatchback_type_id(db), service_id=star)
    )
    return sub["id"]


async def remaining(db, sub_id: str) -> int:
    return int((await db.user_subscriptions.find_one({"_id": ObjectId(sub_id)}))["remaining_service_count"])


async def assign(db, booking_id: str, s: dict) -> None:
    from app.schemas.booking_schema import BookingAssignCaptainRequest

    await BookingService(db).assign_captain(
        booking_id, BookingAssignCaptainRequest(captain_id=s["cap"]["id"]), s["mgr"]["id"], "manager", s["center_id"],
    )


async def queued(db, user_id: str, event: str) -> list[dict]:
    return await db.whatsapp_queue.find({"user_id": user_id, "wa_event": event}).to_list(None)


async def bell(db, user_id: str, title: str | None = None) -> list[dict]:
    q = {"user_id": user_id}
    if title:
        q["title"] = title
    return await db.notifications.find(q).to_list(None)


async def addon(db, name_part: str) -> dict:
    return await db.services.find_one({"name": {"$regex": name_part, "$options": "i"}, "is_active": True})


async def services(db, *, addon: bool | None = None) -> list[dict]:
    q: dict = {"is_active": True, "is_deleted": {"$ne": True}}
    if addon is not None:
        q["is_addon"] = addon
    return await db.services.find(q).to_list(None)


__all__ = [
    "BookingCreateRequest", "BookingService", "datetime", "timedelta", "timezone", "get_suv_type_id",
    "get_hatchback_type_id", "get_star_wash_service_id", "h", "hb",
]
