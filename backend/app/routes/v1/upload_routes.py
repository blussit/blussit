from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers.upload_controller import UploadController
from app.core.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/uploads", tags=["Uploads"])


@router.post("/photo")
async def upload_photo(file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user)):
    """Any authenticated user can upload a photo — captains use this for
    before/after service proof. Returns a URL to store on the record
    instead of ever embedding image bytes in a MongoDB document (see
    app.core.storage for why that matters)."""
    return await UploadController().upload_photo(file)
