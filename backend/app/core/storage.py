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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import BadRequestException

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

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.put(
            url,
            content=data,
            headers={
                "Authorization": authorization,
                "Content-Type": content_type,
                "X-Amz-Content-SHA256": payload_hash,
                "X-Amz-Date": amz_date,
            },
        )
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
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url, headers={
            "Authorization": authorization,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
        })
    if response.status_code == 404:
        raise BadRequestException("Document not found")
    if response.status_code >= 300:
        raise BadRequestException("Document could not be retrieved")
    return response.content, response.headers.get("content-type", "application/octet-stream")
