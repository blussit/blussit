"""
Festival / broadcast campaigns (Admin -> WhatsApp -> Campaigns).

One image + one message to a chosen part of the customer base through the
MARKETING template `blussit_festival_greeting` (whatsapp_campaign_template).

Shape of the machinery:
  * `wa_campaigns` - one row per campaign: details, status
    (draft -> queued -> sending -> done | cancelled | stopped), the frozen
    audience `selection`, the confirmed recipient count, drain lease.
  * `wa_campaign_recipients` - one row per person, unique on
    (campaign_id, user_id) so enqueueing twice can never double a person.
    queued -> sending (120 s lease) -> sent | failed | skipped; Meta's status
    webhook later moves a sent row to delivered / read / failed.
  * The API never loops over the audience: "Send" validates, freezes the
    selection and returns. The WORKER (`run_pass`, from main.py's campaign
    loop) turns the selection into recipient rows in batches of 1 000
    (resumable by _id cursor), then drains them at the token-bucket rate. A
    crash or redeploy loses nothing: leases expire, a row that was already
    handed to Meta is detected through its outbox row instead of re-sent.
  * Selection = {mode all|none|ids, filter, excluded_ids, included_ids};
    eligibility (customer, active, valid 10-digit mobile, not opted out, not
    a staff / business number) is part of EVERY resolution and re-checked
    when the message is actually sent.

Consent: the only marketing consent the app tracks is the opt-OUT flag
`users.marketing_opt_out` (set by "STOP", Meta's 131050 and the profile
switch). There is no explicit opt-in, so "eligible" means "not opted out".
"""
import asyncio
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument, UpdateOne
from pymongo.errors import BulkWriteError

from app.core.config import settings
from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.schemas.whatsapp_campaign_schema import AudienceFilter, Selection
from app.services.whatsapp_campaign_template import (
    FESTIVAL_TEMPLATE_LANGUAGE,
    festival_template_name,
    render_message,
    sanitize_message,
)
from app.services.whatsapp_service import SendOutcome, get_whatsapp_provider, mask_phone
from app.utils.phone import is_business_whatsapp_number, validate_indian_mobile

logger = logging.getLogger(__name__)

CAMPAIGNS = "wa_campaigns"
RECIPIENTS = "wa_campaign_recipients"
STATE = "wa_campaign_state"
SETTINGS_KEY = "whatsapp_campaign_settings"
IST = ZoneInfo("Asia/Kolkata")

ACTIVE_STATUSES = ("queued", "sending")
LEASE_SECONDS = 120
DRAIN_LEASE_SECONDS = 45
MATERIALIZE_BATCH = 1000
SEND_BATCH = 20
MAX_ATTEMPTS = 3
QUERY_MS = 20000

# Meta error codes (Cloud API error reference).
# This person can't be messaged - the campaign carries on without them.
PERSON_ERRORS = {
    131026: "not_on_whatsapp",   # undeliverable: not a WhatsApp number / can't receive
    131021: "invalid_number",    # recipient is the sender
    131030: "invalid_number",    # not in the allowed list (test numbers)
    100: "invalid_number",       # invalid parameter (bad phone shape)
    131050: "opted_out",         # user stopped marketing messages
    131049: "not_delivered_by_meta",  # Meta withheld it to keep the ecosystem healthy
    131047: "window_closed",
    130472: "experiment_holdout",
}
# Everything after this would fail the same way - stop the whole campaign.
STOP_ERRORS = {
    132000: "Meta rejected the template parameters (count mismatch)",
    132001: "The template does not exist in this language",
    132005: "Meta could not translate the template text",
    132007: "The template content breaks WhatsApp policy",
    132012: "Template parameter format mismatch",
    132015: "The template is paused by Meta (quality)",
    132016: "The template is disabled by Meta",
    131048: "Meta's spam rate limit was hit - the sender quality is at risk",
    131042: "A business payment problem blocks sending (check Meta billing)",
    131031: "The WhatsApp account is locked or restricted",
    368: "The WhatsApp account is temporarily blocked for policy violations",
    131056: None,  # per-pair rate limit: handled as retry below
}
RETRY_ERRORS = {130429: "throughput", 131056: "pair_rate_limit", 80007: "rate_limit"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def ensure_campaign_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[CAMPAIGNS].create_index([("created_at", DESCENDING)])
    await db[CAMPAIGNS].create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    r = db[RECIPIENTS]
    await r.create_index([("campaign_id", 1), ("user_id", 1)], unique=True, name="uniq_campaign_recipient")
    await r.create_index([("campaign_id", 1), ("status", 1), ("next_at", 1), ("_id", 1)], name="campaign_claim")
    await r.create_index([("campaign_id", 1), ("status", 1), ("lease_until", 1)], name="campaign_lease")
    await r.create_index("wamid", sparse=True)
    await r.create_index("sent_at", sparse=True)
    await r.create_index("created_at", expireAfterSeconds=400 * 24 * 3600)
    await db.whatsapp_outbox.create_index("campaign_recipient_id", sparse=True)


# ----------------------------------------------------------------------
# Limits (env defaults; a cost override already stored in `settings` is honoured)
# ----------------------------------------------------------------------

async def get_limits(db: AsyncIOMotorDatabase) -> dict:
    doc = await db.settings.find_one({"key": SETTINGS_KEY}, {"value": 1})
    value = (doc or {}).get("value") or {}
    cost = value.get("whatsapp_marketing_cost_paise")
    return {
        "whatsapp_marketing_cost_paise": int(cost) if isinstance(cost, int) and cost >= 0 else int(settings.WHATSAPP_MARKETING_COST_PAISE),
        "rate_per_second": max(1, int(settings.WHATSAPP_CAMPAIGN_RATE_PER_SECOND)),
        "max_recipients": max(1, int(settings.WHATSAPP_CAMPAIGN_MAX_RECIPIENTS)),
        "daily_cap": max(1, int(settings.WHATSAPP_CAMPAIGN_DAILY_CAP)),
    }


# ----------------------------------------------------------------------
# Audience
# ----------------------------------------------------------------------

def _oids(values: list[str]) -> list[ObjectId]:
    out = []
    for v in values or []:
        if isinstance(v, str) and ObjectId.is_valid(v):
            out.append(ObjectId(v))
    return out


def _day_start_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=IST).astimezone(timezone.utc)


async def _staff_phone_exclusions(db: AsyncIOMotorDatabase) -> list[str]:
    phones = await db.users.distinct("phone", {"role": {"$ne": "customer"}, "phone": {"$type": "string"}})
    out = set()
    for p in phones:
        local = validate_indian_mobile(p)
        if local:
            out.update({local, f"91{local}"})
    business = validate_indian_mobile(settings.WHATSAPP_BUSINESS_NUMBER or "")
    if business:
        out.update({business, f"91{business}"})
    return sorted(out)


async def eligible_match(db: AsyncIOMotorDatabase) -> dict:
    """The people a marketing broadcast may ever reach."""
    staff = await _staff_phone_exclusions(db)
    phone_cond: dict = {"$regex": r"^[6-9][0-9]{9}$"}
    if staff:
        phone_cond["$nin"] = staff
    return {
        "role": "customer",
        "status": "active",
        "is_deleted": {"$ne": True},
        "marketing_opt_out": {"$ne": True},
        "phone": phone_cond,
    }


async def filter_conditions(db: AsyncIOMotorDatabase, f: AudienceFilter) -> list[dict]:
    conds: list[dict] = []
    search = (f.search or "").strip()
    if search:
        digits = re.sub(r"\D", "", search)
        if digits and len(digits) >= 3 and re.fullmatch(r"[\d\s+\-]+", search):
            conds.append({"phone": {"$regex": f"{re.escape(digits[-10:] if len(digits) > 10 else digits)}"}})
        else:
            conds.append({"full_name": {"$regex": re.escape(search), "$options": "i"}})
    if f.center_id:
        conds.append({"home_center_id": f.center_id})
    if f.city:
        conds.append({"home_city": {"$regex": f"^{re.escape(f.city.strip())}$", "$options": "i"}})
    if f.no_booking:
        conds.append({"$or": [{"last_booking_at": None}, {"last_booking_at": {"$exists": False}}]})
    else:
        rng: dict = {}
        if f.last_booking_from:
            rng["$gte"] = _day_start_utc(f.last_booking_from)
        if f.last_booking_to:
            rng["$lt"] = _day_start_utc(f.last_booking_to + timedelta(days=1))
        if rng:
            conds.append({"last_booking_at": rng})
    if f.bookings_min:
        conds.append({"bookings_count": {"$gte": f.bookings_min}})
    if f.bookings_max is not None:
        conds.append({"$or": [{"bookings_count": {"$lte": f.bookings_max}}, {"bookings_count": {"$exists": False}}]})
    if f.has_active_plan is not None:
        ids = await db.user_subscriptions.distinct(
            "customer_id", {"status": "active", "is_deleted": {"$ne": True}}
        )
        oids = _oids([str(i) for i in ids])
        conds.append({"_id": {"$in": oids} if f.has_active_plan else {"$nin": oids}})
    return conds


async def selection_match(db: AsyncIOMotorDatabase, sel: Selection, *, after_id: ObjectId | None = None) -> dict | None:
    """The Mongo filter for a selection; None = provably nobody."""
    base = await eligible_match(db)
    conds: list[dict] = [base]
    if sel.mode == "all":
        conds += await filter_conditions(db, sel.filter)
        excluded = _oids(sel.excluded_ids)
        if excluded:
            conds.append({"_id": {"$nin": excluded}})
    else:
        included = _oids(sel.included_ids)
        if not included:
            return None
        conds.append({"_id": {"$in": included}})
    if after_id is not None:
        conds.append({"_id": {"$gt": after_id}})
    return {"$and": conds}


async def count_selection(db: AsyncIOMotorDatabase, sel: Selection) -> int:
    match = await selection_match(db, sel)
    if match is None:
        return 0
    return await db.users.count_documents(match, maxTimeMS=QUERY_MS)


async def list_audience(
    db: AsyncIOMotorDatabase, f: AudienceFilter, sel: Selection | None, page: int, page_size: int, after: str | None = None,
) -> dict:
    base = await eligible_match(db)
    conds = [base, *await filter_conditions(db, f)]
    match = {"$and": conds}
    total = await db.users.count_documents(match, maxTimeMS=QUERY_MS)
    page_size = max(1, min(int(page_size), 100))
    page = max(1, int(page))
    query = dict(match)
    skip = 0
    if after and ObjectId.is_valid(after):
        query = {"$and": [*conds, {"_id": {"$gt": ObjectId(after)}}]}
    else:
        skip = (page - 1) * page_size
    cursor = db.users.find(
        query, {"full_name": 1, "phone": 1, "home_city": 1, "home_center_id": 1, "last_booking_at": 1, "bookings_count": 1},
        max_time_ms=QUERY_MS,
    ).sort("_id", 1).skip(skip).limit(page_size)
    rows = await cursor.to_list(page_size)
    ids = [str(r["_id"]) for r in rows]
    plan_holders = set()
    if ids:
        plan_holders = set(await db.user_subscriptions.distinct(
            "customer_id", {"customer_id": {"$in": ids}, "status": "active", "is_deleted": {"$ne": True}}
        ))
    centers = {str(c["_id"]): c.get("name") for c in await db.service_centers.find({}, {"name": 1}).to_list(500)}
    excluded = set(sel.excluded_ids) if sel else set()
    included = set(sel.included_ids) if sel else set()
    out = []
    for r in rows:
        uid = str(r["_id"])
        selected = (uid not in excluded) if (sel and sel.mode == "all") else (uid in included)
        out.append({
            "id": uid, "name": r.get("full_name") or "", "phone": r.get("phone"), "city": r.get("home_city"),
            "center": centers.get(r.get("home_center_id") or ""), "center_id": r.get("home_center_id"),
            "last_booking_at": _aware(r.get("last_booking_at")).isoformat() if r.get("last_booking_at") else None,
            "bookings_count": int(r.get("bookings_count") or 0), "has_active_plan": uid in plan_holders, "selected": selected,
        })
    return {
        "items": out, "total": total, "page": page, "page_size": page_size,
        "next_after": ids[-1] if len(ids) == page_size else None,
    }


# ----------------------------------------------------------------------
# Campaign documents
# ----------------------------------------------------------------------

def _public(c: dict) -> dict:
    def iso(v):
        return _aware(v).isoformat() if v else None

    return {
        "id": str(c["_id"]), "name": c.get("name"), "template_name": c.get("template_name"), "image_url": c.get("image_url"),
        "message": c.get("message"), "rendered_message": render_message(c.get("message") or ""), "status": c.get("status"),
        "created_by": c.get("created_by"), "created_by_name": c.get("created_by_name"), "created_at": iso(c.get("created_at")),
        "started_at": iso(c.get("started_at")), "finished_at": iso(c.get("finished_at")), "cancelled_at": iso(c.get("cancelled_at")),
        "stop_reason": c.get("stop_reason"), "waiting_reason": c.get("waiting_reason"),
        "total_recipients": int(c.get("total_recipients") or 0), "cost_estimate_paise": int(c.get("cost_estimate_paise") or 0),
        "counts": c.get("counts") or _empty_counts(), "selection_summary": c.get("selection_summary"),
    }


def _empty_counts() -> dict:
    return {"queued": 0, "sent": 0, "delivered": 0, "read": 0, "failed": 0, "skipped": 0}


async def validate_image_url(db: AsyncIOMotorDatabase, url: str) -> str:
    """Only a picture uploaded through the campaign upload (recorded in
    `uploaded_photos`); Meta fetches it, so with the real provider it must
    be a public https URL."""
    from app.core.storage import PHOTO_RECORDS, photo_key_for_url

    key = photo_key_for_url(url)
    if not key or not await db[PHOTO_RECORDS].find_one({"_id": key}, {"_id": 1}):
        raise BadRequestException("Upload the festival image first - use the image upload")
    if settings.WHATSAPP_PROVIDER == "meta_cloud":
        host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
        if not url.startswith("https://") or host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith(("10.", "192.168.")):
            raise BadRequestException("WhatsApp can only fetch a public https image - set up public image storage (R2) first")
    return url


async def create_draft(db: AsyncIOMotorDatabase, actor, name: str, message: str, image_url: str) -> dict:
    doc = {
        "name": name.strip(), "template_name": festival_template_name(), "template_language": FESTIVAL_TEMPLATE_LANGUAGE,
        "image_url": await validate_image_url(db, image_url), "message": sanitize_message(message), "status": "draft",
        "created_by": actor.id, "created_by_name": getattr(actor, "full_name", None) or getattr(actor, "name", None),
        "created_at": _utcnow(), "counts": _empty_counts(), "total_recipients": 0,
    }
    doc["_id"] = (await db[CAMPAIGNS].insert_one(doc)).inserted_id
    return doc


async def get_campaign(db: AsyncIOMotorDatabase, campaign_id: str) -> dict:
    if not ObjectId.is_valid(campaign_id):
        raise NotFoundException("Campaign not found")
    c = await db[CAMPAIGNS].find_one({"_id": ObjectId(campaign_id)})
    if not c:
        raise NotFoundException("Campaign not found")
    return c


async def update_draft(db: AsyncIOMotorDatabase, campaign_id: str, changes: dict) -> dict:
    c = await get_campaign(db, campaign_id)
    if c["status"] != "draft":
        raise ConflictException("Only a draft can be edited")
    sets: dict = {}
    if changes.get("name") is not None:
        sets["name"] = changes["name"].strip()
    if changes.get("message") is not None:
        sets["message"] = sanitize_message(changes["message"])
    if changes.get("image_url") is not None:
        sets["image_url"] = await validate_image_url(db, changes["image_url"])
    if sets:
        await db[CAMPAIGNS].update_one({"_id": c["_id"], "status": "draft"}, {"$set": sets})
    return await get_campaign(db, campaign_id)


async def delete_draft(db: AsyncIOMotorDatabase, campaign_id: str) -> None:
    c = await get_campaign(db, campaign_id)
    if c["status"] != "draft":
        raise ConflictException("Only a draft can be deleted")
    await db[CAMPAIGNS].delete_one({"_id": c["_id"], "status": "draft"})


async def list_campaigns(db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20) -> dict:
    page_size = max(1, min(page_size, 100))
    total = await db[CAMPAIGNS].count_documents({})
    rows = await db[CAMPAIGNS].find({}).sort("created_at", -1).skip((max(1, page) - 1) * page_size).limit(page_size).to_list(page_size)
    items = []
    for c in rows:
        if c["status"] in ACTIVE_STATUSES:
            c["counts"] = await recount(db, c["_id"], persist=False)
        items.append(_public(c))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def recount(db: AsyncIOMotorDatabase, campaign_oid: ObjectId, persist: bool = True) -> dict:
    """Counts straight from the recipient rows (the source of truth)."""
    counts = _empty_counts()
    async for g in db[RECIPIENTS].aggregate([
        {"$match": {"campaign_id": campaign_oid}},
        {"$group": {"_id": {"s": "$status", "r": {"$ifNull": ["$delivery_rank", 0]}}, "n": {"$sum": 1}}},
    ], maxTimeMS=QUERY_MS):
        status, rank, n = g["_id"]["s"], g["_id"]["r"], g["n"]
        if status in ("queued", "sending"):
            counts["queued"] += n
        elif status == "sent":
            counts["sent"] += n
            if rank >= 1:
                counts["delivered"] += n
            if rank >= 2:
                counts["read"] += n
        elif status == "failed":
            counts["failed"] += n
        elif status == "skipped":
            counts["skipped"] += n
    if persist:
        await db[CAMPAIGNS].update_one({"_id": campaign_oid}, {"$set": {"counts": counts}})
    return counts


async def progress(db: AsyncIOMotorDatabase, campaign_id: str) -> dict:
    c = await get_campaign(db, campaign_id)
    c["counts"] = await recount(db, c["_id"], persist=c["status"] not in ACTIVE_STATUSES and c["status"] != "draft")
    out = _public(c)
    out["materialized_count"] = int(c.get("materialized_count") or 0)
    out["failure_reasons"] = {
        g["_id"] or "unknown": g["n"]
        async for g in db[RECIPIENTS].aggregate([
            {"$match": {"campaign_id": c["_id"], "status": {"$in": ["failed", "skipped"]}}},
            {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
        ], maxTimeMS=QUERY_MS)
    }
    return out


async def list_recipients(db: AsyncIOMotorDatabase, campaign_id: str, status: str | None, page: int, page_size: int) -> dict:
    c = await get_campaign(db, campaign_id)
    q: dict = {"campaign_id": c["_id"]}
    if status in ("queued", "sending", "sent", "failed", "skipped"):
        q["status"] = status
    page_size = max(1, min(page_size, 100))
    total = await db[RECIPIENTS].count_documents(q, maxTimeMS=QUERY_MS)
    rows = await db[RECIPIENTS].find(q).sort("_id", 1).skip((max(1, page) - 1) * page_size).limit(page_size).to_list(page_size)
    return {
        "items": [{
            "id": str(r["_id"]), "user_id": r.get("user_id"), "phone": mask_phone(r.get("phone")), "status": r.get("status"),
            "delivery_status": r.get("delivery_status"), "reason": r.get("reason"), "error": r.get("error"),
            "attempts": r.get("attempts", 0),
        } for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


# ----------------------------------------------------------------------
# Template readiness, quote, test, send, cancel
# ----------------------------------------------------------------------

async def template_state(db: AsyncIOMotorDatabase) -> dict:
    """Is the festival template usable now? The log provider needs no
    approval; with Meta the row must be APPROVED and not switched off."""
    name = festival_template_name()
    if settings.WHATSAPP_PROVIDER != "meta_cloud":
        return {"name": name, "ready": True, "status": "LOG_PROVIDER", "note": "Dev mode - messages are only logged"}
    row = await db.whatsapp_templates.find_one({"name": name}, {"status": 1, "disabled": 1, "language": 1, "rejected_reason": 1})
    if not row:
        return {"name": name, "ready": False, "status": "NOT_SUBMITTED", "note": "Submit the template to Meta first"}
    ready = row.get("status") == "APPROVED" and not row.get("disabled")
    return {"name": name, "ready": ready, "status": row.get("status") or "UNKNOWN", "language": row.get("language"),
            "note": "" if ready else (row.get("rejected_reason") or f"Template is {row.get('status')}")}


async def _sent_last_24h(db: AsyncIOMotorDatabase) -> int:
    return await db[RECIPIENTS].count_documents({"sent_at": {"$gte": _utcnow() - timedelta(hours=24)}}, maxTimeMS=QUERY_MS)


async def quote(db: AsyncIOMotorDatabase, campaign: dict, sel: Selection) -> dict:
    limits = await get_limits(db)
    count = await count_selection(db, sel)
    sent_24h = await _sent_last_24h(db)
    tpl = await template_state(db)
    problems = []
    if count == 0:
        problems.append("Nobody is selected")
    if count > limits["max_recipients"]:
        problems.append(f"{count} people is above the per-campaign limit of {limits['max_recipients']}")
    if count + sent_24h > limits["daily_cap"]:
        problems.append(f"The daily limit is {limits['daily_cap']} messages and {sent_24h} already went out in the last 24 hours")
    if not tpl["ready"]:
        problems.append(f"The template is not ready ({tpl['status']})")
    running = await _other_running(db, campaign["_id"])
    if running:
        problems.append(f"Another campaign is running ({running.get('name')})")
    return {
        "recipient_count": count, "cost_paise": count * limits["whatsapp_marketing_cost_paise"],
        "cost_per_message_paise": limits["whatsapp_marketing_cost_paise"],
        "eta_seconds": int(count / limits["rate_per_second"]) + (1 if count else 0), "rate_per_second": limits["rate_per_second"],
        "max_recipients": limits["max_recipients"], "daily_cap": limits["daily_cap"], "sent_last_24h": sent_24h,
        "template": tpl, "problems": problems, "can_send": not problems,
        "image_url": campaign["image_url"], "message": campaign["message"], "rendered_message": render_message(campaign["message"]),
        "consent_note": "Marketing goes to customers who have not opted out (STOP). Staff, invalid and inactive numbers are always left out.",
    }


async def _other_running(db: AsyncIOMotorDatabase, campaign_oid: ObjectId) -> dict | None:
    return await db[CAMPAIGNS].find_one(
        {"status": {"$in": [*ACTIVE_STATUSES, "enqueuing"]}, "_id": {"$ne": campaign_oid}}, {"name": 1}
    )


async def send_test(db: AsyncIOMotorDatabase, actor, campaign_id: str) -> dict:
    """One real send of this campaign to the admin's OWN number."""
    c = await get_campaign(db, campaign_id)
    me = await db.users.find_one({"_id": ObjectId(actor.id)}, {"phone": 1}) if ObjectId.is_valid(actor.id) else None
    phone = validate_indian_mobile((me or {}).get("phone") or "")
    if not phone:
        raise BadRequestException("Add your own mobile number to your profile first")
    tpl = await template_state(db)
    if not tpl["ready"]:
        raise BadRequestException(f"The template is not ready ({tpl['status']}) - Meta must approve it first")
    recent = await db.whatsapp_outbox.count_documents(
        {"kind": "campaign_test", "created_at": {"$gte": _utcnow() - timedelta(hours=1)}, "tester_id": actor.id}
    )
    if recent >= 10:
        raise BadRequestException("Too many test messages this hour - try again later")
    outcome = await get_whatsapp_provider(db).send_header_image_template_outcome(
        phone, c["template_name"], c.get("template_language") or FESTIVAL_TEMPLATE_LANGUAGE, c["image_url"], [c["message"]],
        extra={"kind": "campaign_test", "campaign_id": str(c["_id"]), "tester_id": actor.id},
    )
    if not outcome.ok:
        raise BadRequestException(f"WhatsApp did not accept the test message ({outcome.error or outcome.reason or 'unknown error'})")
    return {"sent_to": mask_phone(phone)}


async def _claim_running_slot(db: AsyncIOMotorDatabase, campaign_oid: ObjectId) -> bool:
    """One running campaign at a time: an atomic claim on a single state row
    (self-healing if its holder is gone or no longer active)."""
    state = db[STATE]
    await state.update_one({"_id": "running"}, {"$setOnInsert": {"campaign_id": None}}, upsert=True)
    got = await state.update_one({"_id": "running", "campaign_id": None}, {"$set": {"campaign_id": campaign_oid, "at": _utcnow()}})
    if got.modified_count:
        return True
    holder = (await state.find_one({"_id": "running"})) or {}
    if holder.get("campaign_id") == campaign_oid:
        return True
    live = await db[CAMPAIGNS].find_one(
        {"_id": holder.get("campaign_id"), "status": {"$in": [*ACTIVE_STATUSES, "enqueuing"]}}, {"_id": 1}
    )
    if live:
        return False
    swap = await state.update_one(
        {"_id": "running", "campaign_id": holder.get("campaign_id")}, {"$set": {"campaign_id": campaign_oid, "at": _utcnow()}}
    )
    return bool(swap.modified_count)


async def _release_running_slot(db: AsyncIOMotorDatabase, campaign_oid: ObjectId) -> None:
    await db[STATE].update_one({"_id": "running", "campaign_id": campaign_oid}, {"$set": {"campaign_id": None}})


async def send_campaign(db: AsyncIOMotorDatabase, actor, campaign_id: str, sel: Selection, confirm_count: int) -> dict:
    """The single fire. Validates, freezes the selection, flips the campaign
    to `queued` and returns - the worker does the rest. Calling it again for
    a campaign that is already past draft changes nothing (retry-safe)."""
    c = await get_campaign(db, campaign_id)
    if c["status"] != "draft":
        if c["status"] in (*ACTIVE_STATUSES, "done"):
            return {**_public(c), "already_started": True}
        raise ConflictException(f"This campaign is {c['status']} and cannot be sent")
    q = await quote(db, c, sel)
    if not q["can_send"]:
        raise BadRequestException("; ".join(q["problems"]))
    if confirm_count != q["recipient_count"]:
        raise ConflictException(f"The audience is now {q['recipient_count']} people, not {confirm_count} - review and confirm again")
    if not await _claim_running_slot(db, c["_id"]):
        raise ConflictException("Another campaign is running - wait for it to finish or cancel it")
    now = _utcnow()
    limits = await get_limits(db)
    snapshot = sel.model_dump(mode="json")
    doc = await db[CAMPAIGNS].find_one_and_update(
        {"_id": c["_id"], "status": "draft"},
        {"$set": {
            "status": "queued", "queued_at": now, "queued_by": actor.id, "selection": snapshot,
            "selection_summary": {
                "mode": sel.mode, "excluded": len(sel.excluded_ids), "included": len(sel.included_ids),
                "filter": sel.filter.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
            },
            "total_recipients": q["recipient_count"], "cost_estimate_paise": q["cost_paise"], "rate_per_second": limits["rate_per_second"],
            "materialized": False, "materialized_through": None, "materialized_count": 0, "counts": {**_empty_counts(), "queued": q["recipient_count"]},
        }},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:  # a concurrent send won the draft
        again = await get_campaign(db, campaign_id)
        return {**_public(again), "already_started": True}
    return _public(doc)


async def cancel_campaign(db: AsyncIOMotorDatabase, actor, campaign_id: str) -> dict:
    c = await get_campaign(db, campaign_id)
    if c["status"] == "draft":
        raise ConflictException("A draft has nothing to cancel - delete it instead")
    if c["status"] not in ACTIVE_STATUSES:
        return {**_public(c), "already_finished": True}
    now = _utcnow()
    await db[CAMPAIGNS].update_one(
        {"_id": c["_id"], "status": {"$in": list(ACTIVE_STATUSES)}},
        {"$set": {"status": "cancelled", "cancelled_at": now, "cancelled_by": actor.id, "finished_at": now}},
    )
    await db[RECIPIENTS].update_many(
        {"campaign_id": c["_id"], "status": "queued"}, {"$set": {"status": "skipped", "reason": "cancelled", "updated_at": now}}
    )
    await _release_running_slot(db, c["_id"])
    await recount(db, c["_id"])
    return _public(await get_campaign(db, campaign_id))


# ----------------------------------------------------------------------
# Worker: materialise recipients, drain at the token-bucket rate
# ----------------------------------------------------------------------

class TokenBucket:
    """Classic token bucket: `rate` tokens/second, burst of one second."""

    def __init__(self, rate: float):
        self.rate = max(0.1, float(rate))
        self.capacity = max(1.0, self.rate)
        self.tokens = self.capacity
        self.stamp = time.monotonic()

    async def acquire(self, n: int) -> None:
        n = min(n, self.capacity)
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now
            if self.tokens >= n:
                self.tokens -= n
                return
            await asyncio.sleep((n - self.tokens) / self.rate)


async def materialize_batch(db: AsyncIOMotorDatabase, c: dict, limit: int = MATERIALIZE_BATCH) -> int:
    """Turns the next `limit` selected people into recipient rows (never more
    than the count the admin confirmed). Resumable: the cursor is the last
    user _id handled. Returns how many rows were added; sets `materialized`
    when the audience is exhausted."""
    if c.get("materialized"):
        return 0
    remaining = int(c.get("total_recipients") or 0) - int(c.get("materialized_count") or 0)
    if remaining <= 0:
        await db[CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": {"materialized": True}})
        c["materialized"] = True
        return 0
    sel = Selection(**c["selection"])
    after = ObjectId(c["materialized_through"]) if c.get("materialized_through") else None
    match = await selection_match(db, sel, after_id=after)
    users = [] if match is None else await db.users.find(match, {"phone": 1}).sort("_id", 1).limit(min(limit, remaining)).to_list(limit)
    if not users:
        await db[CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": {"materialized": True}})
        c["materialized"] = True
        return 0
    now = _utcnow()
    docs = [{
        "campaign_id": c["_id"], "user_id": str(u["_id"]), "phone": u["phone"], "status": "queued", "attempts": 0,
        "next_at": now, "created_at": now,
    } for u in users]
    inserted = 0
    try:
        inserted = len((await db[RECIPIENTS].insert_many(docs, ordered=False)).inserted_ids)
    except BulkWriteError as exc:  # duplicates = already enqueued by an earlier (crashed) pass
        inserted = int(exc.details.get("nInserted", 0))
        if any(e.get("code") != 11000 for e in exc.details.get("writeErrors", [])):
            raise
    last = str(users[-1]["_id"])
    already = await db[RECIPIENTS].count_documents({"campaign_id": c["_id"]}) if inserted < len(docs) else None
    update = {"$set": {"materialized_through": last}}
    if already is not None:
        update["$set"]["materialized_count"] = already
    else:
        update["$inc"] = {"materialized_count": inserted}
    updated = await db[CAMPAIGNS].find_one_and_update({"_id": c["_id"]}, update, return_document=ReturnDocument.AFTER)
    c.update({k: updated.get(k) for k in ("materialized_through", "materialized_count")})
    if len(users) < min(limit, remaining) or int(updated.get("materialized_count") or 0) >= int(c.get("total_recipients") or 0):
        await db[CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": {"materialized": True}})
        c["materialized"] = True
    return inserted


async def _claim_batch(db: AsyncIOMotorDatabase, campaign_oid: ObjectId, owner: str, n: int) -> list[dict]:
    now = _utcnow()
    col = db[RECIPIENTS]
    ids = [d["_id"] async for d in col.find(
        {"campaign_id": campaign_oid, "status": "sending", "lease_until": {"$lt": now}}, {"_id": 1}
    ).limit(n)]
    expired = set(ids)
    if len(ids) < n:
        ids += [d["_id"] async for d in col.find(
            {"campaign_id": campaign_oid, "status": "queued", "next_at": {"$lte": now}}, {"_id": 1}
        ).sort([("next_at", 1), ("_id", 1)]).limit(n - len(ids))]
    if not ids:
        return []
    await col.update_many(
        {"_id": {"$in": ids}, "$or": [{"status": "queued"}, {"status": "sending", "lease_until": {"$lt": now}}]},
        {"$set": {"status": "sending", "lease_owner": owner, "lease_until": now + timedelta(seconds=LEASE_SECONDS), "updated_at": now},
         "$inc": {"attempts": 1}},
    )
    rows = await col.find({"_id": {"$in": ids}, "lease_owner": owner, "status": "sending"}).to_list(len(ids))
    for r in rows:
        r["_recovered"] = r["_id"] in expired
    return rows


def classify(outcome: SendOutcome) -> tuple[str, str | None]:
    """(kind, reason): ok | retry | failed | skip | stop."""
    if outcome.ok:
        return "ok", None
    code = outcome.error_code
    if outcome.reason == "auth":
        return "stop", "Meta refused the access token - fix the WhatsApp credentials"
    if outcome.reason == "business_number":
        return "skip", "business_number"
    if code in RETRY_ERRORS or outcome.transport_failure:
        return "retry", RETRY_ERRORS.get(code, "transport")
    if code in STOP_ERRORS and STOP_ERRORS[code]:
        return "stop", STOP_ERRORS[code]
    if code in PERSON_ERRORS:
        return "failed", PERSON_ERRORS[code]
    return "failed", "rejected"


async def _send_one(provider, c: dict, rec: dict, phone: str) -> tuple[SendOutcome, dict]:
    extra = {"kind": "campaign", "campaign_id": str(c["_id"]), "campaign_recipient_id": str(rec["_id"])}
    try:
        outcome = await provider.send_header_image_template_outcome(
            phone, c["template_name"], c.get("template_language") or FESTIVAL_TEMPLATE_LANGUAGE, c["image_url"], [c["message"]], extra=extra,
        )
    except Exception as exc:  # noqa: BLE001 - one bad send never kills the pass
        logger.exception("Campaign send raised for %s", mask_phone(phone))
        outcome = SendOutcome(False, transport_failure=True, error=str(exc)[:200])
    return outcome, extra


async def _drain_batch(db: AsyncIOMotorDatabase, provider, c: dict, rows: list[dict], owner: str) -> dict:
    """Sends one claimed batch and records the verdicts. Returns
    {"stop": reason|None, "retries": n, "sent": n}."""
    now = _utcnow()
    users = {
        str(u["_id"]): u async for u in db.users.find(
            {"_id": {"$in": _oids([r["user_id"] for r in rows])}}, {"phone": 1, "status": 1, "marketing_opt_out": 1, "is_deleted": 1, "role": 1}
        )
    }
    staff = set(await _staff_phone_exclusions(db))
    ops: list[UpdateOne] = []
    tasks, task_rows = [], []
    sent = retries = 0
    stop: str | None = None

    def finish(rec, sets):
        ops.append(UpdateOne({"_id": rec["_id"], "status": "sending", "lease_owner": owner}, {"$set": {**sets, "updated_at": now}, "$unset": {"lease_until": "", "lease_owner": ""}}))

    for rec in rows:
        u = users.get(rec["user_id"])
        phone = validate_indian_mobile((u or {}).get("phone") or "")
        if not u or u.get("is_deleted") or u.get("status") != "active" or u.get("role") != "customer":
            finish(rec, {"status": "skipped", "reason": "inactive"})
        elif u.get("marketing_opt_out"):
            finish(rec, {"status": "skipped", "reason": "opted_out"})
        elif not phone:
            finish(rec, {"status": "skipped", "reason": "invalid_number"})
        elif phone in staff or f"91{phone}" in staff or is_business_whatsapp_number(phone):
            finish(rec, {"status": "skipped", "reason": "staff_number"})
        elif rec.get("_recovered") and (prev := await db.whatsapp_outbox.find_one(
                {"campaign_recipient_id": str(rec["_id"]), "ok": {"$ne": False}}, {"wamid": 1})):
            # The previous owner died after Meta took the message: never send twice.
            finish(rec, {"status": "sent", "sent_at": now, "wamid": prev.get("wamid"), "recovered": True})
            sent += 1
        else:
            tasks.append(_send_one(provider, c, rec, phone))
            task_rows.append(rec)
    results = await asyncio.gather(*tasks) if tasks else []
    for rec, (outcome, _extra) in zip(task_rows, results):
        kind, reason = classify(outcome)
        if kind == "ok":
            finish(rec, {"status": "sent", "sent_at": now, "wamid": outcome.wamid})
            sent += 1
        elif kind == "retry":
            if rec.get("attempts", 1) >= MAX_ATTEMPTS:
                finish(rec, {"status": "failed", "reason": reason, "error": outcome.error, "failed_at": now})
            else:
                retries += 1
                ops.append(UpdateOne(
                    {"_id": rec["_id"], "status": "sending", "lease_owner": owner},
                    {"$set": {"status": "queued", "next_at": now + timedelta(seconds=30 * rec.get("attempts", 1)), "reason": reason, "updated_at": now},
                     "$unset": {"lease_until": "", "lease_owner": ""}},
                ))
        elif kind == "skip":
            finish(rec, {"status": "skipped", "reason": reason})
        elif kind == "stop":
            stop = stop or reason
            ops.append(UpdateOne(
                {"_id": rec["_id"], "status": "sending", "lease_owner": owner},
                {"$set": {"status": "queued", "next_at": now, "updated_at": now}, "$unset": {"lease_until": "", "lease_owner": ""}},
            ))
        else:
            finish(rec, {"status": "failed", "reason": reason, "error": outcome.error, "failed_at": now, "error_code": outcome.error_code})
    if ops:
        await db[RECIPIENTS].bulk_write(ops, ordered=False)
    return {"stop": stop, "retries": retries, "sent": sent}


async def stop_campaign(db: AsyncIOMotorDatabase, campaign_oid: ObjectId, reason: str) -> None:
    now = _utcnow()
    logger.error("WhatsApp campaign %s stopped: %s", campaign_oid, reason)
    res = await db[CAMPAIGNS].update_one(
        {"_id": campaign_oid, "status": {"$in": list(ACTIVE_STATUSES)}},
        {"$set": {"status": "stopped", "stop_reason": reason, "finished_at": now}},
    )
    if res.modified_count:
        await db[RECIPIENTS].update_many(
            {"campaign_id": campaign_oid, "status": {"$in": ["queued", "sending"]}},
            {"$set": {"status": "skipped", "reason": "campaign_stopped", "updated_at": now}, "$unset": {"lease_until": "", "lease_owner": ""}},
        )
        await _release_running_slot(db, campaign_oid)
        await recount(db, campaign_oid)


async def _finish_if_drained(db: AsyncIOMotorDatabase, c: dict) -> bool:
    if not c.get("materialized"):
        return False
    open_rows = await db[RECIPIENTS].count_documents({"campaign_id": c["_id"], "status": {"$in": ["queued", "sending"]}})
    if open_rows:
        return False
    res = await db[CAMPAIGNS].update_one(
        {"_id": c["_id"], "status": {"$in": list(ACTIVE_STATUSES)}}, {"$set": {"status": "done", "finished_at": _utcnow(), "waiting_reason": None}}
    )
    if res.modified_count:
        await _release_running_slot(db, c["_id"])
        await recount(db, c["_id"])
    return True


async def run_pass(db: AsyncIOMotorDatabase, budget_seconds: float = 20.0, bucket: TokenBucket | None = None, owner: str | None = None) -> dict:
    """One worker slice: advance the running campaign for up to
    `budget_seconds`. Safe on any number of instances (drain lease) and
    after any crash (resumable). Returns what happened."""
    owner = owner or uuid.uuid4().hex
    stats = {"campaign": None, "materialized": 0, "sent": 0, "status": None}
    now = _utcnow()
    c = await db[CAMPAIGNS].find_one_and_update(
        {"status": {"$in": list(ACTIVE_STATUSES)}, "$or": [{"drain_lease_until": None}, {"drain_lease_until": {"$lt": now}}, {"drain_owner": owner}]},
        {"$set": {"drain_owner": owner, "drain_lease_until": now + timedelta(seconds=DRAIN_LEASE_SECONDS)}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER,
    )
    if c is None:
        return stats
    stats["campaign"] = str(c["_id"])
    limits = await get_limits(db)
    bucket = bucket or TokenBucket(limits["rate_per_second"])
    provider = get_whatsapp_provider(db)
    deadline = time.monotonic() + budget_seconds
    if c["status"] == "queued":
        await db[CAMPAIGNS].update_one({"_id": c["_id"], "status": "queued"}, {"$set": {"status": "sending", "started_at": c.get("started_at") or now}})
        c["status"] = "sending"
    while time.monotonic() < deadline:
        c = await db[CAMPAIGNS].find_one({"_id": c["_id"]})
        if not c or c["status"] not in ACTIVE_STATUSES:
            break
        await db[CAMPAIGNS].update_one(
            {"_id": c["_id"]}, {"$set": {"drain_lease_until": _utcnow() + timedelta(seconds=DRAIN_LEASE_SECONDS)}}
        )
        tpl = await template_state(db)
        if not tpl["ready"]:
            await stop_campaign(db, c["_id"], f"The template stopped being usable ({tpl['status']})")
            break
        if not c.get("materialized"):
            stats["materialized"] += await materialize_batch(db, c)
        n = SEND_BATCH
        cap_left = limits["daily_cap"] - await _sent_last_24h(db)
        if cap_left <= 0:
            await db[CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": {"waiting_reason": "daily_cap"}})
            stats["status"] = "waiting_daily_cap"
            break
        n = min(n, cap_left)
        rows = await _claim_batch(db, c["_id"], owner, n)
        if not rows:
            if await _finish_if_drained(db, c):
                stats["status"] = "done"
                break
            if c.get("materialized"):
                await asyncio.sleep(0.2)  # rows waiting out a retry delay or another owner's lease
                if time.monotonic() + 1 > deadline:
                    break
            continue
        await bucket.acquire(len(rows))
        result = await _drain_batch(db, provider, c, rows, owner)
        stats["sent"] += result["sent"]
        if result["stop"]:
            await stop_campaign(db, c["_id"], result["stop"])
            stats["status"] = "stopped"
            break
        if result["retries"] == len(rows):
            await asyncio.sleep(min(5.0, max(0.0, deadline - time.monotonic())))  # Meta is pushing back
        await db[CAMPAIGNS].update_one({"_id": c["_id"]}, {"$unset": {"waiting_reason": ""}})
    await db[CAMPAIGNS].update_one({"_id": c["_id"], "drain_owner": owner}, {"$set": {"drain_lease_until": None}})
    if stats["status"] is None:
        cur = await db[CAMPAIGNS].find_one({"_id": c["_id"]}, {"status": 1})
        stats["status"] = (cur or {}).get("status")
        if cur and cur["status"] in ACTIVE_STATUSES:
            await recount(db, c["_id"])
    return stats


# ----------------------------------------------------------------------
# Meta status webhook -> recipient
# ----------------------------------------------------------------------

async def apply_status(db: AsyncIOMotorDatabase, wamid: str | None, status: str | None, errors: list[dict] | None = None) -> bool:
    """delivered / read / failed for a campaign message. Cheap for every other
    message (one indexed miss). Never raises (webhook path)."""
    if not wamid or status not in ("delivered", "read", "failed"):
        return False
    try:
        now = _utcnow()
        col = db[RECIPIENTS]
        if status == "failed":
            first = (errors or [{}])[0] or {}
            code = first.get("code")
            reason = PERSON_ERRORS.get(code, "undelivered")
            error = " ".join(str(x) for x in (code, first.get("title") or first.get("message")) if x)[:300]
            res = await col.update_one(
                {"wamid": wamid, "status": "sent", "delivery_rank": {"$not": {"$gte": 1}}},
                {"$set": {"status": "failed", "reason": reason, "error": error or "Delivery failed", "delivery_status": "failed", "failed_at": now}},
            )
            return bool(res.modified_count)
        rank = 1 if status == "delivered" else 2
        res = await col.update_one(
            {"wamid": wamid, "status": "sent", "delivery_rank": {"$not": {"$gte": rank}}},
            {"$set": {"delivery_status": status, "delivery_rank": rank, f"{status}_at": now}},
        )
        return bool(res.modified_count)
    except Exception:  # noqa: BLE001
        logger.exception("Could not apply campaign delivery status %s", status)
        return False
