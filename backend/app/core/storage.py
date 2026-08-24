"""
Minimal storage abstraction for user-uploaded files (captain before/after
photos today, and anything else that needs a real file upload later).

STORAGE_PROVIDER selects the backend. "local" (the only one implemented so
far) writes to disk under UPLOAD_DIR and serves the file back through the
app's own StaticFiles mount (see app.main) — fine for a single dev/staging
instance. "cloudinary" is the intended Phase 2 provider for once this needs
to run across multiple app instances or survive redeploys without a
persistent disk; not implemented yet.

Whatever the provider, the contract is the same: give it raw bytes, get
back a short public URL. Only that URL string is ever meant to touch a
MongoDB document — never the raw file bytes. That distinction matters: it
was skipped for captain photos (the frontend sent a base64 data-URI
straight into a booking's `image_url` field), which ballooned each booking
document to ~1.4MB. MongoDB happily stored that, but it made every
full-document read on the bookings collection punishingly slow — an
index-only op like count_documents stayed instant, while find() (needed for
every booking list — admin, manager, captain, customer) had to physically
pull each 1.4MB document off disk and became slow enough to look hung,
and only gets worse as more bookings/photos accumulate. Routing uploads
through here instead keeps every booking document a few KB regardless of
how many photos exist.
"""
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import BadRequestException

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB — generous for a phone-camera photo, not for abuse


def upload_root() -> Path:
    return Path(settings.UPLOAD_DIR)


async def save_photo_upload(file: UploadFile) -> str:
    """Validates and persists one uploaded photo, returns its public URL."""
    ext = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
    if not ext:
        raise BadRequestException("Only JPEG, PNG, WEBP, or HEIC photos are accepted")

    data = await file.read()
    if not data:
        raise BadRequestException("The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise BadRequestException("Photo is too large (max 8MB)")

    if settings.STORAGE_PROVIDER != "local":
        raise NotImplementedError(f"Storage provider '{settings.STORAGE_PROVIDER}' is not implemented yet")

    subdir = upload_root() / "photos"
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (subdir / filename).write_bytes(data)

    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/uploads/photos/{filename}"
