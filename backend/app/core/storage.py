"""
Minimal storage abstraction for user-uploaded files (captain before/after
photos today, and anything else that needs a real file upload later).

STORAGE_PROVIDER selects the backend. "local" (the only one implemented so
far) writes to disk under UPLOAD_DIR and serves the file back through the
app's own StaticFiles mount (see app.main) — fine for a single dev/staging
instance. "cloudinary" uploads to Cloudinary (signed REST call, no SDK
dependency) and returns the CDN URL — required for any host with an
ephemeral filesystem, where local uploads vanish on every deploy.

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
import hashlib
import time
import uuid
from pathlib import Path

import httpx
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


# Magic-byte prefixes for the accepted formats — the client's Content-Type
# header is attacker-controlled; the first bytes of the file are not.
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),  # RIFF....WEBP — refined below
)


def _sniff_image_ext(head: bytes) -> str | None:
    for prefix, ext in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    # HEIC/HEIF: ISO-BMFF "ftyp" box with a heic/heif/mif1 brand.
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"heif", b"mif1", b"msf1"):
        return "heic"
    return None


async def save_photo_upload(file: UploadFile) -> str:
    """Validates and persists one uploaded photo, returns its public URL."""
    if not ALLOWED_CONTENT_TYPES.get(file.content_type or ""):
        raise BadRequestException("Only JPEG, PNG, WEBP, or HEIC photos are accepted")

    # Stream in chunks and abort the moment the cap is crossed — reading the
    # whole body first meant a multi-GB POST sat fully in RAM before the 8MB
    # check ever ran (an easy OOM on a small single-worker box).
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_UPLOAD_BYTES:
            raise BadRequestException("Photo is too large (max 8MB)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise BadRequestException("The uploaded file is empty")

    # The stored extension comes from the actual bytes, never the header —
    # an HTML/script payload labeled image/png must not land on our origin.
    ext = _sniff_image_ext(data[:16])
    if not ext:
        raise BadRequestException("That file doesn't look like a photo — please upload a real JPEG/PNG/WEBP/HEIC image")

    if settings.STORAGE_PROVIDER == "cloudinary":
        return await _cloudinary_upload(data, ext)
    if settings.STORAGE_PROVIDER != "local":
        raise BadRequestException(f"Storage provider '{settings.STORAGE_PROVIDER}' is not configured")

    subdir = upload_root() / "photos"
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (subdir / filename).write_bytes(data)

    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/uploads/photos/{filename}"


async def _cloudinary_upload(data: bytes, ext: str) -> str:
    """Signed upload via Cloudinary's REST API — no SDK needed. The
    signature is SHA-1 over the sorted param string + API secret, exactly
    per their docs. Returns the https CDN URL."""
    cloud = settings.CLOUDINARY_CLOUD_NAME
    key = settings.CLOUDINARY_API_KEY
    secret = settings.CLOUDINARY_API_SECRET
    if not (cloud and key and secret):
        raise BadRequestException("Cloudinary storage selected but credentials are not configured")
    public_id = f"blussit/photos/{uuid.uuid4().hex}"
    timestamp = str(int(time.time()))
    to_sign = f"public_id={public_id}&timestamp={timestamp}{secret}"
    signature = hashlib.sha1(to_sign.encode()).hexdigest()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
            data={"api_key": key, "timestamp": timestamp, "public_id": public_id, "signature": signature},
            files={"file": (f"photo.{ext}", data)},
        )
    if response.status_code >= 300:
        raise BadRequestException("Photo upload to storage failed — please try again")
    url = response.json().get("secure_url")
    if not url:
        raise BadRequestException("Photo upload to storage failed — please try again")
    return url
