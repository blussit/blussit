"""
NOTIFY feature pass (2026-10-07) — templates and the two customer sweeps:

  (j) bootstrap submits only the CURRENT template set; a name Meta REJECTED
      is never resubmitted — the next version name is used, and the event
      picks up whichever version gets approved;
  spec 1.8 — every event other agents call has a template + fallback;
  review requests — completed AND paid, ~2 h later, 10 AM–7 PM, once,
      only with a Google review URL, opt-out respected if MARKETING.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services import whatsapp_crm_service as crm_mod
from app.services.notification_service import REVIEW_REQUEST_COOLDOWN_DAYS
from app.services.notification_service import NotificationService
from app.services.whatsapp_crm_service import (
    CURRENT_TEMPLATE_DEFS,
    EVENT_TEMPLATES,
    SUPERSEDED_TEMPLATE_NAMES,
    WhatsAppCrmService,
    bootstrap_blussit_templates,
)
from app.services.whatsapp_service import WhatsAppService
from app.utils.timezone import IST

from tests.factories import get_star_wash_service_id, make_customer

pytestmark = pytest.mark.asyncio

REVIEW_URL = "https://g.page/r/CblussitFeatTest/review"
NEW_EVENTS = {
    "booking_edited": 6, "wallet_credited": 4, "wallet_debited": 4, "payment_failed": 3,
    "booking_cancelled_v5": 5, "review_request_google": 2, "universal_message": 2, "book_now": 1,
    "plan_expiring_v2": 4, "manager_new_booking_v2": 6,
}


@pytest.fixture
def submitted(monkeypatch):
    """Captures create_template payloads instead of calling Meta."""
    payloads: list[dict] = []

    async def fake_create(self, payload):
        payloads.append(payload)
        return {"id": "1", "status": "PENDING", "category": payload.get("category")}

    async def fake_sync(self):
        return {"synced": 0}

    monkeypatch.setattr(WhatsAppService, "create_template", fake_create)
    monkeypatch.setattr(WhatsAppCrmService, "sync_templates", fake_sync)
    return payloads


async def _clear_templates(db, cleanup):
    """Bootstrap writes rows for every current template — all removed after."""
    names = [d["name"] for d in CURRENT_TEMPLATE_DEFS] + ["blussit_otp"]
    cleanup.append(("whatsapp_templates", {"name": {"$in": names}}))
    await db.whatsapp_templates.delete_many({"name": {"$in": names}})


async def _set_review_url(db, cleanup, url: str = REVIEW_URL):
    cleanup.append(("settings", {"key": "whatsapp_settings"}))
    cleanup.append(("settings_history", {"key": "whatsapp_settings"}))
    await WhatsAppCrmService(db).update_settings({"google_review_url": url}, actor_id="test")


# ---------------------------------------------------------------- definitions
def test_every_new_event_has_a_current_template_with_matching_params():
    defs = {d["name"]: d for d in CURRENT_TEMPLATE_DEFS}
    for event, params in NEW_EVENTS.items():
        name = EVENT_TEMPLATES[event]
        assert name in defs, event
        d = defs[name]
        assert d["body"].count("{{") == params, (event, d["body"])
        assert len(d["examples"]) == params
        assert not d["body"].strip().startswith("{{") and not d["body"].strip().endswith("}}"), name
        if d.get("button_text"):
            assert len(d["button_text"]) <= 25
            assert all(w[0].isupper() for w in d["button_text"].split()), d["button_text"]  # Title Case
    assert defs[EVENT_TEMPLATES["book_now"]]["category"] == "MARKETING"
    assert defs[EVENT_TEMPLATES["book_now"]]["button_url"] == "https://blussit.com/book"
    assert defs[EVENT_TEMPLATES["plan_expiring_v2"]]["button_url"] == "https://blussit.com/app/subscriptions"
    assert defs[EVENT_TEMPLATES["manager_new_booking_v2"]]["button_url"] == "https://blussit.com/manager/bookings"
    assert "Team Blussit" in defs[EVENT_TEMPLATES["universal_message"]]["body"]
    # The pre-slot reminder is mapped to a current template.
    assert EVENT_TEMPLATES["booking_reminder"] in defs
    # No current template is a superseded one.
    assert not set(defs) & set(SUPERSEDED_TEMPLATE_NAMES)


# ---------------------------------------------------------------- (j)
async def test_bootstrap_submits_only_current_and_bumps_rejected(db, cleanup, submitted):
    await _clear_templates(db, cleanup)
    await _set_review_url(db, cleanup)
    edited = EVENT_TEMPLATES["booking_edited"]
    assert edited.endswith("_v1")
    bumped = edited[:-1] + "2"
    await db.whatsapp_templates.insert_one({"name": edited, "status": "REJECTED", "rejected_reason": "INVALID_FORMAT", "body": "x"})
    cleanup.append(("whatsapp_templates", {"name": {"$in": [edited, bumped]}}))
    approved = EVENT_TEMPLATES["booking_confirmed"]
    await db.whatsapp_templates.insert_one({"name": approved, "status": "APPROVED", "body": "x"})
    cleanup.append(("whatsapp_templates", {"name": approved}))

    results = await bootstrap_blussit_templates(db)
    names = [p["name"] for p in submitted]
    assert not set(names) & set(SUPERSEDED_TEMPLATE_NAMES), "superseded legacy templates are never submitted"
    assert edited not in names, "a REJECTED name is never resubmitted"
    assert bumped in names
    assert approved not in names
    assert len(names) == len(set(names))
    by_name = {r["name"]: r for r in results}
    assert by_name[approved]["note"] == "already exists"
    # Review template: Google URL from the admin setting.
    review = next(p for p in submitted if p["name"] == EVENT_TEMPLATES["review_request_google"])
    buttons = next(c for c in review["components"] if c["type"] == "BUTTONS")["buttons"]
    assert buttons[0]["url"] == REVIEW_URL
    # Real examples, not "Sample 1".
    body = next(c for c in review["components"] if c["type"] == "BODY")
    assert "Sample" not in str(body["example"])
    # The event follows the bumped version once Meta approves it.
    crm = WhatsAppCrmService(db)
    assert await crm.event_template_if_ready("booking_edited") is None
    await db.whatsapp_templates.update_one({"name": bumped}, {"$set": {"status": "APPROVED"}})
    assert (await crm.event_template_if_ready("booking_edited"))["name"] == bumped


async def test_review_template_waits_for_the_google_url(db, cleanup, submitted):
    await _clear_templates(db, cleanup)
    cleanup.append(("settings", {"key": "whatsapp_settings"}))
    await db.settings.delete_many({"key": "whatsapp_settings"})
    results = await bootstrap_blussit_templates(db)
    review = next(r for r in results if r["name"] == EVENT_TEMPLATES["review_request_google"])
    assert review["status"] == "SKIPPED" and "Google review URL" in review["note"]
    assert EVENT_TEMPLATES["review_request_google"] not in [p["name"] for p in submitted]


async def test_catalogue_lists_and_submits_one(db, cleanup, submitted):
    await _clear_templates(db, cleanup)
    crm = WhatsAppCrmService(db)
    catalogue = await crm.template_catalogue()
    keys = {row["key"]: row for row in catalogue}
    book_now = keys[crm_mod.template_key(EVENT_TEMPLATES["book_now"])]
    assert book_now["status"] == "NOT_SUBMITTED" and book_now["submit_name"] == EVENT_TEMPLATES["book_now"]
    assert book_now["category"] == "MARKETING" and "book_now" in book_now["events"]
    result = await crm.submit_catalogue_template(book_now["key"])
    assert result["name"] == EVENT_TEMPLATES["book_now"] and submitted[-1]["category"] == "MARKETING"


async def test_manager_alert_falls_back_from_v2_to_v1(db, cleanup):
    crm = WhatsAppCrmService(db)
    v2, v1 = EVENT_TEMPLATES["manager_new_booking"], "blussit_manager_new_booking_v1"
    assert v2 == EVENT_TEMPLATES["manager_new_booking_v2"] == "blussit_manager_new_booking_v2"
    cleanup.append(("whatsapp_templates", {"name": {"$in": [v1, v2]}}))
    await db.whatsapp_templates.delete_many({"name": {"$in": [v1, v2]}})
    await db.whatsapp_templates.insert_one({"name": v1, "status": "APPROVED", "body": "a", "param_count": 6})
    assert (await crm.event_template_if_ready("manager_new_booking"))["name"] == v1
    await db.whatsapp_templates.insert_one({"name": v2, "status": "APPROVED", "body": "a", "param_count": 6})
    assert (await crm.event_template_if_ready("manager_new_booking"))["name"] == v2


async def test_cancelled_v5_never_falls_back_to_an_incompatible_template(db, cleanup):
    crm = WhatsAppCrmService(db)
    old = "blussit_booking_cancelled_v4"
    cleanup.append(("whatsapp_templates", {"name": old}))
    await db.whatsapp_templates.update_one({"name": old}, {"$set": {"status": "APPROVED", "body": "x"}}, upsert=True)
    await db.whatsapp_templates.delete_many({"name": {"$regex": "^" + EVENT_TEMPLATES["booking_cancelled_v5"][:-3]}})
    # The wallet line must never land in v4's "vehicle" slot.
    assert await crm.event_template_if_ready("booking_cancelled_v5") is None


# ---------------------------------------------------------------- review requests
def _ist(hour: int, minute: int = 0, days: int = 0) -> datetime:
    base = datetime.now(IST).replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days)
    return base


async def _review_setup(db, cleanup, *, category: str = "UTILITY"):
    await _set_review_url(db, cleanup)
    name = EVENT_TEMPLATES["review_request_google"]
    await db.whatsapp_templates.update_one(
        {"name": name},
        {"$set": {"name": name, "status": "APPROVED", "category": category, "language": "en_US", "param_count": 2,
                  "body": "Hi {{1}}, thanks for choosing Blussit for your {{2}}.", "button_url": REVIEW_URL, "disabled": False}},
        upsert=True,
    )
    cleanup.append(("whatsapp_templates", {"name": name}))
    return name


async def _completed_booking(db, cleanup, customer_id: str, *, done_at: datetime, paid: bool = True, **extra) -> str:
    service_id = await get_star_wash_service_id(db)
    doc = {
        "customer_id": customer_id, "status": "completed", "service_ids": [service_id],
        "booking_number": f"BKF{uuid.uuid4().hex[:12].upper()}", "scheduled_date": done_at.replace(tzinfo=None, hour=0, minute=0),
        "scheduled_slot": "09:00-12:00", "completed_at": done_at, "closed_at": done_at,
        "payment_status": "paid" if paid else "pending", "total_amount": 299.0, "is_deleted": False,
        "created_at": done_at - timedelta(days=1), **extra,
    }
    bid = (await db.bookings.insert_one(doc)).inserted_id
    cleanup.append(("bookings", {"_id": bid}))
    cleanup.append(("notification_claims", {"_id": {"$regex": str(bid)}}))
    return str(bid)


async def _customer(db, cleanup, **extra) -> tuple[str, str]:
    cid = await make_customer(db)
    if extra:
        await db.users.update_one({"_id": ObjectId(cid)}, {"$set": extra})
    phone = (await db.users.find_one({"_id": ObjectId(cid)}))["phone"]
    for coll, flt in (
        ("users", {"_id": ObjectId(cid)}), ("notifications", {"user_id": cid}),
        ("whatsapp_outbox", {"phone": phone}), ("whatsapp_queue", {"user_id": cid}),
    ):
        cleanup.append((coll, flt))
    return cid, phone


async def test_review_request_sent_once_for_completed_paid_booking(db, cleanup):
    name = await _review_setup(db, cleanup)
    now = _ist(14)
    cid, phone = await _customer(db, cleanup)
    paid = await _completed_booking(db, cleanup, cid, done_at=now - timedelta(hours=3))
    cid2, phone2 = await _customer(db, cleanup)
    unpaid = await _completed_booking(db, cleanup, cid2, done_at=now - timedelta(hours=3), paid=False)
    cid3, phone3 = await _customer(db, cleanup)
    too_soon = await _completed_booking(db, cleanup, cid3, done_at=now - timedelta(minutes=50))

    service = NotificationService(db)
    stats = await service.send_review_requests(now=now)
    assert stats["sent"] >= 1
    out = await db.whatsapp_outbox.find_one({"phone": phone})
    assert out and out["template_name"] == name
    assert (await db.bookings.find_one({"_id": ObjectId(paid)})).get("review_request_sent_at")
    assert not await db.whatsapp_outbox.find_one({"phone": phone2})
    assert not await db.whatsapp_outbox.find_one({"phone": phone3})
    assert not (await db.bookings.find_one({"_id": ObjectId(unpaid)})).get("review_request_sent_at")
    assert not (await db.bookings.find_one({"_id": ObjectId(too_soon)})).get("review_request_sent_at")
    # Once only.
    await service.send_review_requests(now=now)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1


async def test_review_requests_wait_for_daytime_and_url(db, cleanup):
    await _review_setup(db, cleanup)
    cid, phone = await _customer(db, cleanup)
    await _completed_booking(db, cleanup, cid, done_at=_ist(21) - timedelta(hours=3))
    service = NotificationService(db)
    assert (await service.send_review_requests(now=_ist(21)))["skipped"] == "outside customer hours"
    assert (await service.send_review_requests(now=_ist(8)))["skipped"] == "outside customer hours"
    await WhatsAppCrmService(db).update_settings({"google_review_url": ""}, actor_id="test")
    assert (await service.send_review_requests(now=_ist(12)))["skipped"] == "no google_review_url"
    assert not await db.whatsapp_outbox.find_one({"phone": phone})


async def test_review_request_needs_the_approved_template(db, cleanup):
    await _set_review_url(db, cleanup)
    name = EVENT_TEMPLATES["review_request_google"]
    cleanup.append(("whatsapp_templates", {"name": name}))
    await db.whatsapp_templates.update_one({"name": name}, {"$set": {"status": "PENDING"}}, upsert=True)
    cid, phone = await _customer(db, cleanup)
    bid = await _completed_booking(db, cleanup, cid, done_at=_ist(13) - timedelta(hours=3))
    stats = await NotificationService(db).send_review_requests(now=_ist(13))
    assert stats["skipped"] == "review template not approved"
    assert not (await db.bookings.find_one({"_id": ObjectId(bid)})).get("review_request_sent_at")


async def test_marketing_review_template_skips_opted_out(db, cleanup):
    await _review_setup(db, cleanup, category="MARKETING")
    now = _ist(15)
    cid, phone = await _customer(db, cleanup, marketing_opt_out=True)
    bid = await _completed_booking(db, cleanup, cid, done_at=now - timedelta(hours=4))
    await NotificationService(db).send_review_requests(now=now)
    assert not await db.whatsapp_outbox.find_one({"phone": phone})
    # Decided once: not reconsidered every pass.
    assert (await db.bookings.find_one({"_id": ObjectId(bid)})).get("review_request_sent_at")


async def test_review_sweep_honours_the_worker_checkpoint(db, cleanup):
    await _review_setup(db, cleanup)
    now = _ist(16)
    cid, phone = await _customer(db, cleanup)
    await _completed_booking(db, cleanup, cid, done_at=now - timedelta(hours=3))

    class Stop(Exception):
        pass

    async def checkpoint():
        raise Stop()

    with pytest.raises(Stop):
        await NotificationService(db).send_review_requests(now=now, checkpoint=checkpoint)
    assert not await db.whatsapp_outbox.find_one({"phone": phone})


async def test_review_asked_at_most_once_per_cooldown_per_customer(db, cleanup):
    await _review_setup(db, cleanup)
    now = _ist(12)
    cid, phone = await _customer(db, cleanup)
    first = await _completed_booking(db, cleanup, cid, done_at=now - timedelta(hours=5))
    second = await _completed_booking(db, cleanup, cid, done_at=now - timedelta(hours=3))
    await NotificationService(db).send_review_requests(now=now)
    assert await db.whatsapp_outbox.count_documents({"phone": phone}) == 1
    outcomes = {(await db.bookings.find_one({"_id": ObjectId(b)}))["review_request_outcome"] for b in (first, second)}
    assert outcomes == {"sent", "recently_asked"}
    assert REVIEW_REQUEST_COOLDOWN_DAYS >= 7
