from fastapi import UploadFile

from app.core.responses import success
from app.core.storage import save_document_upload, save_photo_upload


class UploadController:
    """No database access needed — file storage is provider-backed (see
    app.core.storage), kept as its own controller for the same
    routes-call-controllers convention as everything else, not because it
    touches Mongo."""

    async def upload_photo(self, file: UploadFile):
        url = await save_photo_upload(file)
        return success({"url": url}, "Photo uploaded")

    async def upload_document(self, file: UploadFile):
        url = await save_document_upload(file)
        return success({"url": url}, "Document uploaded")
