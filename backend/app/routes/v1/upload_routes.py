from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from urllib.parse import unquote

from app.controllers.upload_controller import UploadController
from app.core.authz import ensure_own_center
from app.core.config import settings
from app.core.dependencies import CurrentUser, get_current_user, get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.core.storage import download_private_document

router = APIRouter(prefix="/uploads", tags=["Uploads"])


@router.post("/photo")
async def upload_photo(file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user)):
    """Any authenticated user can upload a photo — captains use this for
    before/after service proof. Returns a URL to store on the record
    instead of ever embedding image bytes in a MongoDB document (see
    app.core.storage for why that matters)."""
    return await UploadController().upload_photo(file)


@router.post("/document")
async def upload_document(file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user)):
    """Upload a KYC or other identity document to provider-backed storage."""
    return await UploadController().upload_document(file)


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
    owner = await db.users.find_one({
        "$or": [
            {"captain_kyc.aadhaar_doc_url": document_url},
            {"captain_kyc.pan_doc_url": document_url},
        ],
        "is_deleted": {"$ne": True},
    })
    if not owner:
        raise NotFoundException("Document not found")
    if current_user.role == "admin":
        pass
    elif current_user.role == "manager":
        ensure_own_center(current_user.role, current_user.service_center_id, owner.get("service_center_id"))
    elif current_user.id != str(owner["_id"]):
        raise ForbiddenException("You cannot access this document")
    content, content_type = await download_private_document(key)
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "private, no-store"})
