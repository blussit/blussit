"""Follow-up 2026-10-07 — PATCH /bookings/{id} and /bookings/group/{gid}
with `dry_run: true`: the same validation, locks and pricing as the save,
nothing written (no seat move, no money, no messages, no history, no new
saved address) — and the save that follows produces exactly the preview's
numbers."""
import itertools

import pytest
from bson import ObjectId

from app.core.exceptions import BadRequestException
from app.schemas.booking_schema import BookingCreateRequest, BookingEditRequest
from app.services.booking_service import BookingService
from tests import test_feat_booking_helpers as fb
from tests import test_fix_core_helpers as h

pytestmark = pytest.mark.asyncio
_seq = itertools.count(1)
NUMBERS = ("total_amount", "amount_due", "wallet_credit", "travel_charge", "cars")


async def _svc_id(db, slug: str) -> str:
    return str((await db.services.find_one({"slug": slug}))["_id"])


async def _state(db, s: dict, booking_ids: list[str]) -> dict:
    """Everything a dry run must leave alone."""
    cu = s["cu"]["id"]
    return {
        "bookings": [await db.bookings.find_one({"_id": ObjectId(i)}) for i in booking_ids],
        "wallet": await db.customer_wallets.find_one({"customer_id": cu}),
        "ledger": await db.customer_wallet_ledger.count_documents({"customer_id": cu}),
        "seats": await db.slot_capacity.find({"service_center_id": s["center_id"]}, {"_id": 0}).sort([("date", 1), ("slot_key", 1)]).to_list(None),
        "addresses": await db.addresses.count_documents({"owner_id": cu}),
        "history": await db.booking_status_history.count_documents({"booking_id": {"$in": booking_ids}}),
        "bell": await db.notifications.count_documents({"user_id": {"$in": [cu, s["mgr"]["id"]]}}),
        "wa": await db.whatsapp_queue.count_documents({"user_id": cu}),
        "audit": await db.audit_logs.count_documents({"target_id": {"$in": booking_ids}}),
        "users": await db.users.find_one({"_id": ObjectId(cu)}),
    }


async def _preview_then_save(db, s: dict, booking_id: str, body: dict) -> tuple[dict, dict]:
    before = await _state(db, s, [booking_id])
    async with h.client() as c:
        r = await c.patch(f"/api/v1/bookings/{booking_id}", json={**body, "dry_run": True}, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        preview = r.json()["data"]
        assert preview["dry_run"] is True and preview["changes"]
        assert await _state(db, s, [booking_id]) == before, "the dry run wrote something"
        r = await c.patch(f"/api/v1/bookings/{booking_id}", json=body, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        saved = r.json()["data"]
    for key in NUMBERS:
        assert preview[key] == saved[key], key
    assert [ch["field"] for ch in preview["changes"]] == [ch["field"] for ch in saved["changes"]]
    assert preview["notices"] == saved["notices"]
    return preview, saved


async def test_preview_equals_save_for_a_service_upgrade(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    preview, saved = await _preview_then_save(db, s, b["id"], {"service_ids": [await _svc_id(db, "deep-cleaning")]})
    assert preview["total_amount"] > b["total_amount"] and preview["wallet_credit"] == 0
    assert preview["amount_due"] == preview["total_amount"]
    d = await fb.doc(db, b["id"])
    assert d["total_amount"] == preview["total_amount"] and preview["cars"] == [
        # travel_charge per car (2026-10-07 follow-up: the edit answer carries the distance charge).
        {"booking_id": b["id"], "booking_number": d["booking_number"], "total_amount": d["total_amount"], "amount_due": d["amount_due"],
         "travel_charge": float(d.get("travel_charge") or 0)}
    ]


async def test_preview_equals_save_for_a_prepaid_downgrade_with_wallet_credit(db):
    s = await fb.rig(db)
    cu = s["cu"]["id"]
    made = await BookingService(db).create_booking(cu, BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[await _svc_id(db, "deep-cleaning")],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ), _allow_pinless=True)
    await fb.pay_online(db, made["id"])
    paid = (await fb.doc(db, made["id"]))["total_amount"]
    preview, saved = await _preview_then_save(db, s, made["id"], {"service_ids": [await fb.get_star_wash_service_id(db)]})
    assert 0 < preview["wallet_credit"] == pytest.approx(paid - preview["total_amount"])
    assert preview["amount_due"] == 0
    assert await fb.balance(db, cu) == pytest.approx(saved["wallet_credit"])  # credited once — by the save


async def test_preview_equals_save_for_an_address_change_with_a_travel_charge(db):
    s = await fb.rig(db)
    star = await db.services.find_one({"slug": "star-wash"})
    travel_svc = {k: v for k, v in star.items() if k != "_id"}
    n = next(_seq)
    travel_svc.update({"name": f"Preview Travel Wash {n}", "slug": f"preview-travel-wash-{n}", "charges_travel": True, "prepaid_only": False})
    travel_id = str((await db.services.insert_one(travel_svc)).inserted_id)
    made = await BookingService(db).create_booking(s["cu"]["id"], BookingCreateRequest(
        vehicle_type=await fb.get_hatchback_type_id(db), address_id=s["cu"]["address_id"], service_ids=[travel_id],
        scheduled_date=s["when"], scheduled_slot=s["keys"][0], payment_method="cash",
    ), _allow_pinless=True)
    assert not (await fb.doc(db, made["id"])).get("travel_charge")
    # A new pinned address ~9 km north of the center (outside every center's
    # radius, so the pincode keeps it at THIS center; 5 km are free).
    body = {"address": {"line1": "Far Lane 3, Indore", "city": "Indore", "state": "MP", "pincode": s["pin"],
                        "latitude": 22.98, "longitude": 75.8}}
    preview, saved = await _preview_then_save(db, s, made["id"], body)
    d = await fb.doc(db, made["id"])
    assert d["travel_charge"] > 0 and preview["total_amount"] == d["total_amount"]
    assert d["address_snapshot"]["line1"] == "Far Lane 3, Indore"


async def test_preview_runs_the_same_refusals(db):
    s = await fb.rig(db)
    b = await fb.book(db, s["cu"], s["when"], s["keys"][0])
    await fb.slot_in(db, b["id"], 59)
    with pytest.raises(BadRequestException, match="1 hour before"):
        await BookingService(db).edit_booking(b["id"], BookingEditRequest(customer_notes="Gate 2", dry_run=True), s["cu"]["id"], "customer")


async def test_group_preview_moves_no_seat(db):
    s = await fb.rig(db)
    async with h.client() as c:
        gid, ids = await h.group(c, db, s["cu"], s["when"], s["keys"][0], cars=2)
        new_when, new_keys = await h.slot_keys(db, s["center_id"], offset=3)
        body = {"scheduled_date": new_when, "scheduled_slot": new_keys[1]}
        before = await _state(db, s, ids)
        r = await c.patch(f"/api/v1/bookings/group/{gid}", json={**body, "dry_run": True}, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        preview = r.json()["data"]
        assert preview["dry_run"] is True and {car["booking_id"] for car in preview["cars"]} == set(ids)
        assert await _state(db, s, ids) == before
        r = await c.patch(f"/api/v1/bookings/group/{gid}", json=body, headers=s["cu"]["h"])
        assert r.status_code == 200, r.text
        saved = r.json()["data"]
    for key in NUMBERS:
        assert preview[key] == saved[key], key
    assert (await h.seat(db, s["center_id"], new_when, new_keys[1])).get("booked_count") == 1
