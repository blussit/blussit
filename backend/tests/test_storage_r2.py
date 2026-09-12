from types import SimpleNamespace

import pytest

from app.core import storage


class _Response:
    status_code = 200


class _AsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def put(self, url, content, headers):
        self.calls.append((url, content, headers))
        _AsyncClient.last_call = self.calls[-1]
        return _Response()


@pytest.mark.asyncio
async def test_r2_upload_derives_endpoint_and_returns_public_url(monkeypatch):
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "")
    monkeypatch.setattr(storage.settings, "R2_BUCKET_NAME", "blussit-images")
    monkeypatch.setattr(
        storage.settings,
        "R2_PUBLIC_BASE_URL",
        "https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images",
    )
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "access")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(storage.settings, "R2_UPLOAD_PREFIX", "photos")
    monkeypatch.setattr(storage.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(storage.uuid, "uuid4", lambda: SimpleNamespace(hex="abc123"))

    url = await storage._r2_upload(b"\xff\xd8\xffphoto", "jpg", "image/jpeg")

    assert url == "https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images/photos/abc123.jpg"
    upload_url, body, headers = _AsyncClient.last_call
    assert upload_url == "https://d2208b1ea9cea36f384529411277791e.r2.cloudflarestorage.com/blussit-images/photos/abc123.jpg"
    assert body == b"\xff\xd8\xffphoto"
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=access/")
    assert headers["Content-Type"] == "image/jpeg"


class _Upload:
    content_type = "image/png"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, size):
        return self._chunks.pop(0) if self._chunks else b""


class _DocumentUpload(_Upload):
    content_type = "application/pdf"


@pytest.mark.asyncio
async def test_save_photo_upload_uses_r2_provider(monkeypatch):
    monkeypatch.setattr(storage.settings, "STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(storage.settings, "R2_BUCKET_NAME", "blussit-images")
    monkeypatch.setattr(storage.settings, "R2_PUBLIC_BASE_URL", "https://cdn.blussit.test")
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "access")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(storage.settings, "R2_UPLOAD_PREFIX", "profiles")
    monkeypatch.setattr(storage.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(storage.uuid, "uuid4", lambda: SimpleNamespace(hex="profilepic"))

    url = await storage.save_photo_upload(_Upload([b"\x89PNG\r\n\x1a\nimage"]))

    assert url == "https://cdn.blussit.test/profiles/profilepic.png"


@pytest.mark.asyncio
async def test_save_document_upload_returns_protected_api_url(monkeypatch):
    monkeypatch.setattr(storage.settings, "STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(storage.settings, "R2_PRIVATE_BUCKET_NAME", "private-documents")
    monkeypatch.setattr(storage.settings, "PUBLIC_BASE_URL", "https://api.blussit.test")
    monkeypatch.setattr(storage.settings, "API_V1_PREFIX", "/api/v1")

    async def fake_r2_upload(data, ext, content_type, prefix=None, bucket=None, private=False):
        assert bucket == "private-documents"
        assert prefix == "documents"
        assert private is True
        return "documents/private-id.pdf"

    monkeypatch.setattr(storage, "_r2_upload", fake_r2_upload)
    url = await storage.save_document_upload(_DocumentUpload([b"%PDF-1.7\nprivate document"]))

    assert url == "https://api.blussit.test/api/v1/uploads/document/documents/private-id.pdf"
