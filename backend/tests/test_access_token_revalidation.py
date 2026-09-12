from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core import dependencies
from app.core.security import create_access_token


class _Users:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        return self.user


@pytest.mark.asyncio
async def test_access_token_rejects_suspended_user(monkeypatch):
    token = create_access_token("507f1f77bcf86cd799439011", "customer", {"tv": 0})
    monkeypatch.setattr(
        dependencies,
        "get_database",
        lambda: SimpleNamespace(users=_Users({"_id": "id", "role": "customer", "status": "suspended", "token_version": 0})),
    )

    with pytest.raises(dependencies.UnauthorizedException):
        await dependencies.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.mark.asyncio
async def test_access_token_rejects_role_change(monkeypatch):
    token = create_access_token("507f1f77bcf86cd799439011", "customer", {"tv": 0})
    monkeypatch.setattr(
        dependencies,
        "get_database",
        lambda: SimpleNamespace(users=_Users({"_id": "id", "role": "manager", "status": "active", "token_version": 0})),
    )

    with pytest.raises(dependencies.UnauthorizedException):
        await dependencies.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.mark.asyncio
async def test_access_token_revalidation_accepts_current_user(monkeypatch):
    token = create_access_token(
        "507f1f77bcf86cd799439011",
        "customer",
        {"email": "customer@example.com", "phone": "9876543210", "tv": 2},
    )
    monkeypatch.setattr(
        dependencies,
        "get_database",
        lambda: SimpleNamespace(users=_Users({
            "_id": "id",
            "role": "customer",
            "status": "active",
            "token_version": 2,
            "email": "customer@example.com",
            "phone": "9876543210",
        })),
    )

    user = await dependencies.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))

    assert user.id == "507f1f77bcf86cd799439011"
    assert user.role == "customer"
