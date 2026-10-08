"""Shared helpers for the test_fix_coreb_* suites (pre-production audit
2026-10-07, core fix round 2). Builds on the round-1 helpers
(tests/test_fix_core_helpers.py). No tests live here."""
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.schemas.booking_schema import BookingCancelRequest, BookingCreateRequest, PhotoCaptureRequest
from app.services.booking_service import BookingService
from tests import test_fix_core_helpers as h
from tests.factories import get_hatchback_type_id, get_star_wash_service_id, make_recorded_photo_url


async def snap(db, captain_id: str, name: str = "p", lat: float = 22.7, lng: float = 75.8) -> PhotoCaptureRequest:
    """A before/after photo exactly as the app makes one: uploaded through
    POST /uploads/photo, so the upload is recorded for this captain (CAP-02
    — a job step only takes a photo its own captain uploaded)."""
    return PhotoCaptureRequest(image_url=await make_recorded_photo_url(db, captain_id, name), latitude=lat, longitude=lng)


async def new_booking(db, cu: dict, when: str, slot: str, *, method: str = "cash", source: str = "app", apply_charges=None, **kw) -> dict:
    """One car, through the real create path (the customer's own app; with
    source="whatsapp"/"staff" pass apply_charges=True for the bot / staff
    paths that book on the customer's behalf)."""
    return await BookingService(db).create_booking(
        cu["id"],
        BookingCreateRequest(
            vehicle_type=await get_hatchback_type_id(db), address_id=cu["address_id"], service_ids=[await get_star_wash_service_id(db)],
            scheduled_date=when, scheduled_slot=slot, payment_method=method, **kw,
        ),
        source=source,
        _allow_pinless=True,
        apply_charges=apply_charges,
    )


async def slot_in(db, booking_ids: list[str] | str, minutes: float) -> None:
    """Move the booking(s)' slot so it starts `minutes` from now (negative:
    already started) — the late-cancellation tier is read from slot_start."""
    ids = [booking_ids] if isinstance(booking_ids, str) else booking_ids
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await db.bookings.update_many(
        {"_id": {"$in": [ObjectId(i) for i in ids]}}, {"$set": {"slot_start": start, "slot_end": start + timedelta(hours=3)}},
    )


def cancel(reason: str = "Customer asked us to cancel", **kw) -> BookingCancelRequest:
    return BookingCancelRequest(reason=reason, **kw)


async def charges_of(db, customer_id: str) -> list[dict]:
    return await db.customer_charges.find({"customer_id": customer_id}).sort("created_at", 1).to_list(None)


async def booking(db, booking_id: str) -> dict:
    return await db.bookings.find_one({"_id": ObjectId(booking_id)})


async def wallet(db, captain_id: str) -> float:
    return float((await db.captain_wallets.find_one({"captain_id": captain_id}))["balance"])


async def staffed_center(db) -> dict:
    """A center with a manager and a captain, plus a customer there."""
    cid, pin = await h.center(db)
    when, keys = await h.slot_keys(db, cid)
    mgr = await h.manager(db, cid)
    # Like a real center: its named manager is the one flags go to.
    await db.service_centers.update_one({"_id": ObjectId(cid)}, {"$set": {"manager_id": mgr["id"]}})
    return {
        "center_id": cid, "pin": pin, "when": when, "keys": keys,
        "cu": await h.customer(db, pin), "mgr": mgr, "cap": await h.captain(db, cid, wallet=500.0),
    }


async def on_the_way(db, booking_id: str, captain_id: str, **extra) -> None:
    """A job the captain has headed out for (the state verify/photos need)."""
    await db.bookings.update_one(
        {"_id": ObjectId(booking_id)},
        {"$set": {"captain_id": captain_id, "status": "captain_on_the_way", "heading_at": datetime.now(timezone.utc), **extra}},
    )
