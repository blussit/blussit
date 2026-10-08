from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from urllib.parse import unquote

from app.controllers.upload_controller import UploadController
from app.core.authz import ensure_own_center
from app.core.config import settings
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_staff
from app.core.exceptions import ForbiddenException, NotFoundException
from app.core.storage import download_private_document

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
    URL — no owner, so only an admin may open it."""
    record = await db.uploaded_documents.find_one({"key": key})
    if record:
        try:
            owner = await db.users.find_one({"_id": ObjectId(record["owner_id"]), "is_deleted": {"$ne": True}})
        except (InvalidId, TypeError):
            owner = None
        return owner is not None, owner
    refs = await db.users.find(
        {
            "$or": [{"captain_kyc.aadhaar_doc_url": document_url}, {"captain_kyc.pan_doc_url": document_url}],
            "is_deleted": {"$ne": True},
        },
        {"_id": 1, "service_center_id": 1},
    ).to_list(length=2)
    return bool(refs), (refs[0] if len(refs) == 1 else None)


@router.get("/document/{key:path}")
async def download_document(
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Authorize and stream a KYC document from the private R2 bucket."""
    key = unquote(key).strip("/")
    if not key.startswith("documents/") or ".." in key.split("/"):
        raise NotFoundException("Document not found")
    document_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.API_V1_PREFIX}/uploads/document/{key}"
    exists, owner = await _document_owner(db, key, document_url)
    if not exists:
        raise NotFoundException("Document not found")
    if current_user.role == "admin":
        pass
    elif owner is None:
        raise ForbiddenException("You cannot access this document")
    elif current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, owner.get("service_center_id"))
    elif current_user.id != str(owner["_id"]):
        raise ForbiddenException("You cannot access this document")
    content, content_type = await download_private_document(key)
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "private, no-store"})
