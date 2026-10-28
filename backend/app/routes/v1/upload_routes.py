from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from app.controllers.upload_controller import UploadController
from app.core.authz import ensure_own_center
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_staff
from app.core.exceptions import ForbiddenException, NotFoundException, TooManyRequestsException
from app.core.storage import _document_route_base, document_media_type, download_private_document, is_valid_document_key

router = APIRouter(prefix="/uploads", tags=["Uploads"])


# Staff only: captains upload before/after proof and KYC files, managers a
# captain's photo. No customer screen uploads anything, and an open upload
# endpoint was free hosting on our bucket for any signed-up phone number.
@router.post("/photo", dependencies=[Depends(require_staff)])
async def upload_photo(
    file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Captains use this for before/after service proof. Returns a URL to
    store on the record instead of ever embedding image bytes in a MongoDB
    document (see app.core.storage for why that matters)."""
    return await UploadController(db).upload_photo(file, current_user)


@router.post("/document", dependencies=[Depends(require_staff)])
async def upload_document(
    file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Upload a KYC or other identity document to provider-backed storage.
    Who uploaded it is recorded — that record, not whichever packet names
    the URL, decides who may download it."""
    return await UploadController(db).upload_document(file, current_user)


async def _document_owner(db: AsyncIOMotorDatabase, key: str, document_url: str) -> tuple[bool, dict | None]:
    """(exists, owner). The uploader on record owns a document. One uploaded
    before uploads were recorded falls back to the single account whose
    KYC packet names it; if two packets name it, someone copied another's
    URL — no owner, so only an admin may open it. A deleted/soft-deleted
    owner still "exists" as a record (KYC is retained) but only an admin
    may open it."""
    record = await db.uploaded_documents.find_one({"key": key})
    if record:
        try:
            owner = await db.users.find_one({"_id": ObjectId(record["owner_id"])})
        except (InvalidId, TypeError):
            owner = None
        if owner is not None and owner.get("is_deleted"):
            owner = None
        return True, owner
    refs = await db.users.find(
        {
            "$or": [{"captain_kyc.aadhaar_doc_url": document_url}, {"captain_kyc.pan_doc_url": document_url}],
            "is_deleted": {"$ne": True},
        },
        {"_id": 1, "service_center_id": 1},
    ).to_list(length=2)
    return bool(refs), (refs[0] if len(refs) == 1 else None)


# Per-account cap on document opens: a manager reviewing a whole team stays
# far below it, a script walking documents does not.
DOC_DOWNLOADS_PER_WINDOW = 60
DOC_DOWNLOAD_WINDOW = timedelta(minutes=10)


async def _count_download(db: AsyncIOMotorDatabase, user_id: str) -> None:
    now = datetime.now(timezone.utc)
    window = int(now.timestamp() // DOC_DOWNLOAD_WINDOW.total_seconds())
    counter = await db.document_download_counters.find_one_and_update(
        {"_id": f"{user_id}:{window}"},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + 2 * DOC_DOWNLOAD_WINDOW}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if counter["count"] > DOC_DOWNLOADS_PER_WINDOW:
        raise TooManyRequestsException("Too many document views — please wait a few minutes and try again.")


# Everything a browser needs to treat the response as an opaque, uncached,
# unrenderable attachment-ish blob.
_DOCUMENT_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-site",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Accept-Ranges": "none",
    "Vary": "Authorization, Origin",
}


@router.get("/document/{key:path}")
async def download_document(
    key: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Authorize and stream one identity document. Allowed: the uploader,
    the manager of the uploader's center, an admin — nobody else. Every
    open by someone other than the owner (and every refusal) is written to
    the audit trail (module "kyc"). The file is never reachable any other
    way: storage is private, keys are random, nothing is served statically."""
    from app.core.rate_limit import _client_ip
    from app.services.audit_service import AuditService

    key = unquote(key).strip("/")
    if not is_valid_document_key(key):
        raise NotFoundException("Document not found")
    await _count_download(db, current_user.id)
    document_url = f"{_document_route_base()}{key}"
    exists, owner = await _document_owner(db, key, document_url)
    if not exists:
        raise NotFoundException("Document not found")
    ip = _client_ip(request)
    audit = AuditService(db)
    owner_id = str(owner["_id"]) if owner is not None else None
    try:
        if current_user.role == "admin":
            pass
        elif owner is None:
            raise ForbiddenException("You cannot access this document")
        elif current_user.role == "manager":
            ensure_own_center(current_user.role, current_user.service_center_id, owner.get("service_center_id"))
        elif current_user.id != owner_id:
            raise ForbiddenException("You cannot access this document")
    except ForbiddenException:
        # Key + actor + IP only: never the document URL or any ID number.
        await audit.log_action(current_user.id, current_user.role, "KYC_DOCUMENT_ACCESS_DENIED", "kyc", owner_id, {"key": key, "ip": ip})
        raise
    if current_user.id != owner_id:
        await audit.log_action(current_user.id, current_user.role, "VIEW_KYC_DOCUMENT", "kyc", owner_id, {"key": key, "ip": ip})
    content, _ = await download_private_document(key)
    ext = key.rsplit(".", 1)[-1]
    return Response(
        content=content,
        media_type=document_media_type(key),
        headers={**_DOCUMENT_HEADERS, "Content-Disposition": f'inline; filename="document.{ext}"'},
    )
