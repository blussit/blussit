"""
Minimal storage abstraction for user-uploaded files (captain before/after
photos today, and anything else that needs a real file upload later).

STORAGE_PROVIDER selects the backend. "local" writes to disk under UPLOAD_DIR
and serves the file back through the app's own StaticFiles mount (see
app.main) — fine for a single dev/staging instance. "r2" uploads to
Cloudflare R2 (signed S3-compatible PUT, no SDK dependency) and returns the
public object URL — required for any host with an ephemeral filesystem, where
local uploads vanish on every deploy.

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
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import UploadFile
from pymongo import ReturnDocument

from app.core.config import settings
from app.core.exceptions import BadRequestException, StorageUnavailableException
from app.core.http_client import shared_client

logger = logging.getLogger(__name__)

# Where locally-stored uploads are reachable when PUBLIC_BASE_URL isn't
# configured — i.e. a developer running this backend on its default port.
# Production sets PUBLIC_BASE_URL and never falls back here.
LOCAL_FALLBACK_BASE_URL = "http://localhost:8000"

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}
ALLOWED_DOCUMENT_TYPES = {
    **ALLOWED_CONTENT_TYPES,
    "application/pdf": "pdf",
}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB — generous for a phone-camera photo, not for abuse
# Every URL this module hands out is well under this; a stored "photo URL"
# longer than it is not one of ours.
MAX_STORED_URL_LENGTH = 500


def _r2_http() -> httpx.AsyncClient:
    return shared_client("r2_storage", 60)


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

    if settings.STORAGE_PROVIDER == "r2":
        return await _r2_upload(data, ext, file.content_type or "application/octet-stream")
    if settings.STORAGE_PROVIDER != "local":
        raise BadRequestException(f"Storage provider '{settings.STORAGE_PROVIDER}' is not configured")

    subdir = upload_root() / "photos"
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (subdir / filename).write_bytes(data)

    # An ABSOLUTE url: these are rendered by the frontend, which is served
    # from a different origin, so a host-less "/uploads/..." would 404
    # there. Blank config (dev) means this backend's local address.
    base = (settings.PUBLIC_BASE_URL or LOCAL_FALLBACK_BASE_URL).rstrip("/")
    return f"{base}/uploads/photos/{filename}"


async def save_document_upload(file: UploadFile) -> str:
    """Validate and persist an identity document through the configured provider."""
    extension = ALLOWED_DOCUMENT_TYPES.get(file.content_type or "")
    if not extension:
        raise BadRequestException("Only PDF, JPEG, PNG, WEBP, or HEIC documents are accepted")

    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_UPLOAD_BYTES:
            raise BadRequestException("Document is too large (max 8MB)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise BadRequestException("The uploaded document is empty")

    if extension == "pdf" and not data.startswith(b"%PDF-"):
        raise BadRequestException("That file doesn't look like a valid PDF")
    if extension != "pdf" and not _sniff_image_ext(data[:16]):
        raise BadRequestException("That file doesn't look like a valid image")

    if settings.STORAGE_PROVIDER == "r2":
        key = await _r2_upload(
            data,
            extension,
            file.content_type or "application/octet-stream",
            prefix="documents",
            bucket=settings.R2_PRIVATE_BUCKET_NAME,
            private=True,
        )
        return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/uploads/document/{quote(key, safe='/')}"
    if settings.STORAGE_PROVIDER != "local":
        raise BadRequestException(f"Storage provider '{settings.STORAGE_PROVIDER}' is not configured")

    subdir = upload_root() / "documents"
    subdir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    (subdir / filename).write_bytes(data)
    base = (settings.PUBLIC_BASE_URL or LOCAL_FALLBACK_BASE_URL).rstrip("/")
    return f"{base}/uploads/documents/{filename}"


def _local_upload_base() -> str:
    return f"{(settings.PUBLIC_BASE_URL or LOCAL_FALLBACK_BASE_URL).rstrip('/')}/uploads/"


def _document_route_base() -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/uploads/document/"


def is_own_upload_url(url: str | None) -> bool:
    """True only for a URL our own storage handed out: the public R2
    bucket, the protected document-download route, or (local storage, dev)
    this backend's /uploads/ mount. Records that store a photo/document
    URL (before/after photos, KYC) accept nothing else — an arbitrary URL
    there renders a stranger's image in staff screens, or (KYC) names
    someone else's private document."""
    if not url or len(url) > MAX_STORED_URL_LENGTH or any(ch.isspace() for ch in url) or ".." in url:
        return False
    prefixes = []
    if settings.R2_PUBLIC_BASE_URL:
        prefixes.append(f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/")
    if settings.PUBLIC_BASE_URL:
        prefixes.append(_document_route_base())
    if settings.STORAGE_PROVIDER == "local":
        prefixes.append(_local_upload_base())
    return any(url.startswith(p) and len(url) > len(p) for p in prefixes)


def document_key_for_url(url: str) -> str | None:
    """The storage key behind a document URL save_document_upload returned
    ("documents/<id>.<ext>"), or None for any other URL."""
    for base in (_document_route_base() if settings.PUBLIC_BASE_URL else None, _local_upload_base()):
        if base and url.startswith(base):
            key = unquote(url[len(base):]).strip("/")
            if key.startswith("documents/") and ".." not in key.split("/"):
                return key
    return None


def photo_key_for_url(url: str | None) -> str | None:
    """The storage key behind a photo URL save_photo_upload returned — the
    R2 object key ("<prefix>/<id>.<ext>") or, local storage, "photos/<file>"
    — else None (not one of ours)."""
    if not url or not is_own_upload_url(url):
        return None
    bases = []
    if settings.R2_PUBLIC_BASE_URL:
        bases.append(f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/")
    if settings.STORAGE_PROVIDER == "local":
        bases.append(_local_upload_base())
    for base in bases:
        if url.startswith(base):
            key = unquote(url[len(base):]).strip("/")
            if key and ".." not in key.split("/") and not key.startswith("documents/"):
                return key
    return None


# --- Photo upload records (audit CAP-02) ---------------------------------
# Every photo upload is recorded in `uploaded_photos`:
#   {_id: <storage key>, url, uploader_id, role, service_center_id,
#    created_at (UTC), used_by: None | {booking_id, step, at}}
# A captain's before/after photo step CLAIMS its photo with
# claim_uploaded_photo: only the captain who uploaded it, only a recent
# upload, and only once — so one old URL can't serve as both before and
# after photo, or on two bookings, or for another captain.
PHOTO_RECORDS = "uploaded_photos"
PHOTO_CLAIM_MAX_AGE = timedelta(hours=6)


async def record_photo_upload(db, url: str, uploader_id: str, role: str, service_center_id: str | None = None) -> dict:
    """Record who uploaded this photo, when. Raises if it can't — an
    unrecorded photo could never be claimed by a job step anyway."""
    key = photo_key_for_url(url)
    if not key:
        raise BadRequestException("Photo upload to storage failed - please try again")
    doc = {
        "_id": key, "url": url, "uploader_id": uploader_id, "role": role, "service_center_id": service_center_id,
        "created_at": datetime.now(timezone.utc), "used_by": None,
    }
    await db[PHOTO_RECORDS].insert_one(doc)
    return doc


async def claim_uploaded_photo(
    db, url: str | None, captain_id: str, *, booking_id: str, step: str, max_age: timedelta = PHOTO_CLAIM_MAX_AGE,
) -> dict | None:
    """Atomically mark this upload as used by (booking, step) — only when
    `captain_id` uploaded it, within `max_age`, and nothing used it before.
    Returns the claimed record, or None: the caller refuses the photo
    ("take a fresh photo"). One guarded write, so two steps racing for the
    same file can't both win."""
    key = photo_key_for_url(url)
    if not key:
        return None
    now = datetime.now(timezone.utc)
    return await db[PHOTO_RECORDS].find_one_and_update(
        {"_id": key, "uploader_id": captain_id, "created_at": {"$gte": now - max_age}, "used_by": None},
        {"$set": {"used_by": {"booking_id": booking_id, "step": step, "at": now}}},
        return_document=ReturnDocument.AFTER,
    )


def _aws_signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret).encode()
    for part in (date_stamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def _r2_config(bucket_override: str | None = None) -> tuple[str, str, str, str, str]:
    endpoint = settings.R2_ENDPOINT_URL.rstrip("/")
    bucket = (bucket_override or settings.R2_BUCKET_NAME).strip("/")
    public_base = settings.R2_PUBLIC_BASE_URL.rstrip("/")
    access_key = settings.R2_ACCESS_KEY_ID
    secret_key = settings.R2_SECRET_ACCESS_KEY
    if not endpoint and public_base and bucket:
        parsed = urlparse(public_base)
        if parsed.netloc.endswith(".r2.cloudflarestorage.com"):
            endpoint = f"{parsed.scheme}://{parsed.netloc}"
    if not (endpoint and bucket and access_key and secret_key) or (bucket_override is None and not public_base):
        raise BadRequestException("Cloudflare R2 storage selected but credentials are not configured")
    return endpoint, bucket, public_base, access_key, secret_key


async def _r2_upload(
    data: bytes,
    ext: str,
    content_type: str,
    prefix: str | None = None,
    bucket: str | None = None,
    private: bool = False,
) -> str:
    """Signed S3-compatible PUT to Cloudflare R2. R2 accepts the standard
    AWS Signature V4 flow with region "auto"; the object is then read back
    via R2_PUBLIC_BASE_URL."""
    endpoint, bucket, public_base, access_key, secret_key = _r2_config(bucket)
    object_prefix = (prefix if prefix is not None else settings.R2_UPLOAD_PREFIX).strip("/")
    key = f"{object_prefix}/{uuid.uuid4().hex}.{ext}" if object_prefix else f"{uuid.uuid4().hex}.{ext}"

    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BadRequestException("Cloudflare R2 endpoint must be an https URL")

    encoded_bucket = quote(bucket, safe="")
    encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
    canonical_uri = f"/{encoded_bucket}/{encoded_key}"
    url = f"{endpoint}{canonical_uri}"

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = "auto"
    service = "s3"
    payload_hash = hashlib.sha256(data).hexdigest()
    host = parsed.netloc
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    canonical_request = "\n".join(("PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash))
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join((
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ))
    signing_key = _aws_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    try:
        response = await _r2_http().put(
            url,
            content=data,
            headers={
                "Authorization": authorization,
                "Content-Type": content_type,
                "X-Amz-Content-SHA256": payload_hash,
                "X-Amz-Date": amz_date,
            },
        )
    except httpx.HTTPError as exc:  # connect/read timeout, DNS, reset — R2 is down, not the photo
        logger.warning("R2 upload unreachable: %s", type(exc).__name__)
        raise StorageUnavailableException() from exc
    if response.status_code >= 500 or response.status_code == 429:
        logger.warning("R2 upload failed on R2's side: HTTP %s", response.status_code)
        raise StorageUnavailableException()
    if response.status_code >= 300:
        raise BadRequestException("Photo upload to storage failed - please try again")
    if private:
        return key
    return f"{public_base}/{encoded_key}"


async def download_private_document(key: str) -> tuple[bytes, str]:
    """Read one object from the private R2 document bucket."""
    endpoint, bucket, _, access_key, secret_key = _r2_config(settings.R2_PRIVATE_BUCKET_NAME)
    parsed = urlparse(endpoint)
    encoded_bucket = quote(bucket, safe="")
    encoded_key = "/".join(quote(part, safe="") for part in key.split("/"))
    canonical_uri = f"/{encoded_bucket}/{encoded_key}"
    url = f"{endpoint}{canonical_uri}"
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_headers = f"host:{parsed.netloc}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    canonical_request = "\n".join(("GET", canonical_uri, "", canonical_headers, signed_headers, payload_hash))
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join((
        "AWS4-HMAC-SHA256", amz_date, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ))
    signing_key = _aws_signing_key(secret_key, date_stamp, "auto", "s3")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    try:
        response = await _r2_http().get(url, headers={
            "Authorization": authorization,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
        })
    except httpx.HTTPError as exc:
        logger.warning("R2 document fetch unreachable: %s", type(exc).__name__)
        raise StorageUnavailableException("Document storage is unreachable right now — please try again in a minute.") from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise StorageUnavailableException("Document storage is unreachable right now — please try again in a minute.")
    if response.status_code == 404:
        raise BadRequestException("Document not found")
    if response.status_code >= 300:
        raise BadRequestException("Document could not be retrieved")
    return response.content, response.headers.get("content-type", "application/octet-stream")
