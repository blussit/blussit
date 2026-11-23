"""
The admin "inbox" sections that need a red dot in the sidebar: contact
messages, custom-plan requests (plan_enquiries) and coverage requests
(coverage_leads). Each document carries `seen_at`:

  * absent / null  -> NEW (nobody opened it yet)
  * a timestamp    -> seen

New public submissions write `seen_at: null` explicitly; a repeat submission
from the same phone (plan enquiries / coverage leads are upserted per phone)
sets it back to null, because it is a fresh request. The admin list pages
mark what they show as seen (per id) and have a "Mark All As Read" button.

`counts()` is what `GET /admin/nav-badges` returns (cached 15 s per instance
under the "admin_nav" namespace, which every admin-feed ping expires on every
instance). `announce()` is the one call a creator makes: expire the cache and
ping the admin feed so an open console shows the dot without a reload.
"""
from datetime import datetime, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import BadRequestException

# API section name -> collection.
SECTIONS: dict[str, str] = {
    "contact_messages": "contact_messages",
    "plan_enquiries": "plan_enquiries",
    "coverage_requests": "coverage_leads",
}
CACHE_NAMESPACE = "admin_nav"
MAX_IDS = 200

UNSEEN = {"is_deleted": {"$ne": True}, "seen_at": None}


def section_collection(db: AsyncIOMotorDatabase, section: str):
    name = SECTIONS.get(section)
    if name is None:
        raise BadRequestException(f"Unknown section. Use one of: {', '.join(SECTIONS)}.")
    return db[name]


class AdminInboxService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def counts(self) -> dict:
        """New (unseen) entries per section — one indexed count each."""
        return {section: await self.db[name].count_documents(UNSEEN) for section, name in SECTIONS.items()}

    async def mark_seen(self, section: str, ids: list[str] | None, admin_id: str) -> dict:
        """Marks the given ids (or, with no ids, every unseen row of the
        section) as seen. Idempotent: an already-seen row keeps its first
        `seen_at`."""
        collection = section_collection(self.db, section)
        query: dict = dict(UNSEEN)
        if ids is not None:
            if len(ids) > MAX_IDS:
                raise BadRequestException(f"Send at most {MAX_IDS} ids at a time, or none to mark everything.")
            query["_id"] = {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}
        result = await collection.update_many(
            query, {"$set": {"seen_at": datetime.now(timezone.utc), "seen_by": admin_id}},
        )
        counts = await self.counts()
        if result.modified_count:
            await announce(self.db, section)
        return {"marked": result.modified_count, "counts": counts}


async def announce(db: AsyncIOMotorDatabase | None, section: str) -> None:
    """A section changed (new entry / marked seen): drop this instance's
    cached counts and ping the admin feed (which expires every instance's
    cache and makes open consoles refetch). Never raises."""
    try:
        from app.core.ws_manager import manager as ws_manager
        from app.services import report_cache

        report_cache.invalidate(CACHE_NAMESPACE)
        await ws_manager.notify_changed("inbox", users=(), admin=True, section=section)
    except Exception:  # noqa: BLE001 - a side effect must never fail the write
        pass


async def backfill_inbox_seen(db: AsyncIOMotorDatabase) -> dict:
    """One-off (idempotent) boot step: rows that predate `seen_at` count as
    seen, so the first deploy does not light every dot — except custom-plan
    requests still in status "new", which really are untriaged."""
    now = datetime.now(timezone.utc)
    done: dict[str, int] = {}
    for section, name in SECTIONS.items():
        collection = db[name]
        missing: dict = {"seen_at": {"$exists": False}}
        if section == "plan_enquiries":
            r = await collection.update_many({**missing, "status": "new"}, {"$set": {"seen_at": None}})
            done["plan_enquiries_new"] = r.modified_count
        r = await collection.update_many(missing, [{"$set": {"seen_at": {"$ifNull": ["$created_at", now]}}}])
        done[section] = r.modified_count
    return done
