"""
One-off migration: pulls base64 data-URI photos that got stored directly
inside booking documents (before app.core.storage existed) out to disk,
replacing each with a short URL — see app.core.storage's module docstring
for why this matters (it's what made booking-list queries hang).

Safe to re-run: any booking whose before_photo/after_photo.image_url doesn't
start with "data:" is left untouched.

Deliberately migrates one booking _id at a time rather than a single find()
across the whole collection — with documents this oversized, even a
projected multi-document cursor was observed to hang (see KNOWN_ISSUES.md /
the investigation that led to this script), while single-document reads by
_id stayed reliable. _id-only listing is index-covered so it stays cheap
regardless of how large the documents themselves are.

Run from backend/: python -m app.scripts.migrate_inline_photos
"""
import asyncio
import base64
import binascii
import re
import uuid
from pathlib import Path

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

_DATA_URI_RE = re.compile(r"^data:(?P<mime>image/[\w.+-]+);base64,(?P<b64>.+)$", re.DOTALL)
_EXT_BY_MIME = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif"}


def _write_data_uri(data_uri: str, subdir: Path) -> str | None:
    match = _DATA_URI_RE.match(data_uri)
    if not match:
        return None
    mime = match.group("mime")
    ext = _EXT_BY_MIME.get(mime, "jpg")
    try:
        raw = base64.b64decode(match.group("b64"), validate=False)
    except binascii.Error:
        return None
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (subdir / filename).write_bytes(raw)
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/uploads/photos/{filename}"


async def main() -> None:
    client = AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]
    subdir = Path(settings.UPLOAD_DIR) / "photos"

    # _id-only listing is satisfied entirely by the _id index — cheap no
    # matter how bloated the documents themselves are.
    ids = [doc["_id"] async for doc in db.bookings.find({}, {"_id": 1})]
    print(f"{len(ids)} booking(s) to check")

    migrated = 0
    for i, _id in enumerate(ids, 1):
        try:
            booking = await asyncio.wait_for(
                db.bookings.find_one({"_id": _id}, {"before_photo": 1, "after_photo": 1}), timeout=30
            )
        except asyncio.TimeoutError:
            print(f"  [{i}/{len(ids)}] {_id}: TIMED OUT reading this document — skipped, investigate manually")
            continue

        if not booking:
            continue

        update: dict = {}
        for field in ("before_photo", "after_photo"):
            photo = booking.get(field)
            url = photo.get("image_url") if photo else None
            if not url or not url.startswith("data:"):
                continue
            new_url = _write_data_uri(url, subdir)
            if new_url:
                update[f"{field}.image_url"] = new_url
                print(f"  [{i}/{len(ids)}] {_id}: {field} -> {new_url} ({len(url)} chars freed)")
            else:
                print(f"  [{i}/{len(ids)}] {_id}: {field} looked like base64 but couldn't be decoded — left as-is")

        if update:
            await db.bookings.update_one({"_id": _id}, {"$set": update})
            migrated += 1

    print(f"Done. {migrated} booking(s) migrated.")


if __name__ == "__main__":
    asyncio.run(main())
