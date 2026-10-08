from datetime import datetime, timedelta, timezone

from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.dependencies import CurrentUser
from app.core.exceptions import TooManyRequestsException
from app.core.responses import success
from app.core.storage import document_key_for_url, record_photo_upload, save_document_upload, save_photo_upload
from app.utils.timezone import now_ist

# Photos + documents one account may upload per IST day. A captain's busiest
# real day (before/after shots for every car of a society round, plus
# retakes) stays far below it; a script filling our bucket does not.
DAILY_UPLOAD_LIMIT = 300


class UploadController:
    """File bytes go to provider-backed storage (see app.core.storage);
    Mongo only keeps the per-day upload counter and the record of who
    uploaded which file — for identity documents the download route
    authorizes on it, for photos the captain's job steps claim it."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def _count_upload(self, user_id: str) -> None:
        day = now_ist().strftime("%Y-%m-%d")
        counter = await self.db.upload_counters.find_one_and_update(
            {"_id": f"{user_id}:{day}"},
            # Expires on its own (TTL index) a day after the IST day ends.
            {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": datetime.now(timezone.utc) + timedelta(days=2)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter["count"] > DAILY_UPLOAD_LIMIT:
            raise TooManyRequestsException("Upload limit reached for today — please try again tomorrow.")

    async def upload_photo(self, file: UploadFile, current_user: CurrentUser):
        await self._count_upload(current_user.id)
        url = await save_photo_upload(file)
        # Who uploaded which photo, when (CAP-02) — the captain's job steps
        # claim their before/after photo from this record, once.
        await record_photo_upload(self.db, url, current_user.id, current_user.role, current_user.service_center_id)
        return success({"url": url}, "Photo uploaded")

    async def upload_document(self, file: UploadFile, current_user: CurrentUser):
        await self._count_upload(current_user.id)
        url = await save_document_upload(file)
        await self.db.uploaded_documents.insert_one({
            "key": document_key_for_url(url),
            "url": url,
            "owner_id": current_user.id,
            "kind": "identity_document",
            "created_at": datetime.now(timezone.utc),
        })
        return success({"url": url}, "Document uploaded")
