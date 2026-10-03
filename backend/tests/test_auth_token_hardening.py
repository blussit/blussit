"""
JWT hardening (2026-10 admin pass):

- refresh tokens are single-use: rotation with reuse detection. A spent
  token replayed after the short grace window revokes its whole chain
  (family) — the thief AND that device are signed out, other devices are
  not — and the event is audit-logged.
- two tabs refreshing with the same token at the same moment is a benign
  race (grace window), not theft.
- the WebSocket endpoint resolves its user from the DB like every REST
  route: a suspended / logged-out / moved account's token no longer opens
  channels.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.core.exceptions import UnauthorizedException
from app.core.security import decode_token
from app.services.auth_service import AuthService
from tests.factories import make_manager, make_service_center


@pytest.fixture
async def manager(db, cleanup):
    center = await make_service_center(db)
    manager_id = await make_manager(db, center)
    cleanup.append(("users", {"_id": ObjectId(manager_id)}))
    cleanup.append(("service_centers", {"_id": ObjectId(center)}))
    cleanup.append(("spent_refresh_tokens", {"user_id": manager_id}))
    cleanup.append(("refresh_token_families", {"user_id": manager_id}))
    cleanup.append(("audit_logs", {"actor_id": manager_id}))
    user = await db.users.find_one({"_id": ObjectId(manager_id)})
    return user


@pytest.mark.asyncio
async def test_refresh_rotates_and_a_replayed_token_revokes_the_chain(db, manager):
    auth = AuthService(db)
    first = auth._issue_tokens(manager)["refresh_token"]
    family = decode_token(first)["fam"]

    second = (await auth.refresh(first))["refresh_token"]
    assert decode_token(second)["fam"] == family  # same chain
    assert decode_token(second)["jti"] != decode_token(first)["jti"]

    # Pretend the first token was spent long ago — a replay now is theft.
    await db.spent_refresh_tokens.update_one(
        {"_id": decode_token(first)["jti"]}, {"$set": {"spent_at": datetime.now(timezone.utc) - timedelta(minutes=5)}}
    )
    with pytest.raises(UnauthorizedException):
        await auth.refresh(first)
    # The whole chain is dead, including the legitimately rotated token…
    with pytest.raises(UnauthorizedException):
        await auth.refresh(second)
    assert await db.refresh_token_families.find_one({"_id": family, "revoked": True})
    assert await db.audit_logs.find_one({"action": "REFRESH_TOKEN_REUSE", "actor_id": str(manager["_id"])})

    # …but another device's session (a separate login = separate family) lives on.
    other = auth._issue_tokens(manager)["refresh_token"]
    assert (await auth.refresh(other))["access_token"]


@pytest.mark.asyncio
async def test_concurrent_refresh_with_the_same_token_is_not_treated_as_theft(db, manager):
    auth = AuthService(db)
    token = auth._issue_tokens(manager)["refresh_token"]
    a = await auth.refresh(token)
    b = await auth.refresh(token)  # second tab, same instant
    assert a["refresh_token"] and b["refresh_token"]
    assert not await db.refresh_token_families.find_one({"_id": decode_token(token)["fam"], "revoked": True})


@pytest.mark.asyncio
async def test_logout_and_suspension_kill_refresh_and_websocket(db, manager):
    from app.routes.v1.ws_routes import _ws_user

    auth = AuthService(db)
    tokens = auth._issue_tokens(manager)
    user = await _ws_user(tokens["access_token"])
    assert user is not None and user.service_center_id == manager["service_center_id"]

    # Moved to another center: the socket token no longer resolves.
    await db.users.update_one({"_id": manager["_id"]}, {"$set": {"service_center_id": str(ObjectId())}})
    assert await _ws_user(tokens["access_token"]) is None
    await db.users.update_one({"_id": manager["_id"]}, {"$set": {"service_center_id": manager["service_center_id"]}})

    # Suspended: socket and refresh both refuse.
    await db.users.update_one({"_id": manager["_id"]}, {"$set": {"status": "suspended"}})
    assert await _ws_user(tokens["access_token"]) is None
    with pytest.raises(UnauthorizedException):
        await auth.refresh(tokens["refresh_token"])
    await db.users.update_one({"_id": manager["_id"]}, {"$set": {"status": "active"}})

    # Logout bumps token_version: everything issued before is dead.
    await auth.logout(str(manager["_id"]))
    assert await _ws_user(tokens["access_token"]) is None
    with pytest.raises(UnauthorizedException):
        await auth.refresh(tokens["refresh_token"])


@pytest.mark.asyncio
async def test_unlinked_manager_never_opens_a_center_channel(db, manager):
    from app.routes.v1.ws_routes import _WsUser, _authorize_channel

    unlinked = _WsUser(id=str(manager["_id"]), role="manager", service_center_id=None)
    assert not await _authorize_channel(f"center-bookings:{ObjectId()}", unlinked, db)
    captain = await db.users.insert_one({"role": "captain", "service_center_id": None, "full_name": "Loose", "is_deleted": False})
    try:
        assert not await _authorize_channel(f"captain-location:{captain.inserted_id}", unlinked, db)
    finally:
        await db.users.delete_one({"_id": captain.inserted_id})


@pytest.mark.asyncio
async def test_admin_cannot_lock_themselves_out_and_suspension_revokes_sessions(db, manager, cleanup):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    admin = await db.users.find_one({"role": "admin", "status": {"$ne": "suspended"}})
    admin_id = str(admin["_id"])
    headers = {"Authorization": f"Bearer {create_access_token(admin_id, 'admin', {'service_center_id': admin.get('service_center_id'), 'tv': admin.get('token_version', 0)})}"}
    tokens = AuthService(db)._issue_tokens(manager)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, url, body in (
            ("POST", f"/api/v1/users/{admin_id}/suspend", None),
            ("DELETE", f"/api/v1/users/{admin_id}", None),
            ("PUT", f"/api/v1/users/{admin_id}", {"role": "manager"}),
            ("PUT", f"/api/v1/users/{admin_id}", {"status": "suspended"}),
        ):
            res = await client.request(method, url, json=body, headers=headers)
            assert res.status_code == 400, f"{method} {url} -> {res.status_code}"
        assert (await db.users.find_one({"_id": admin["_id"]}))["status"] != "suspended"

        # Suspending someone else signs them out everywhere — and a later
        # reactivation does NOT revive the old refresh token.
        res = await client.post(f"/api/v1/users/{manager['_id']}/suspend", headers=headers)
        assert res.status_code == 200, res.text
        res = await client.put(f"/api/v1/users/{manager['_id']}", json={"status": "active"}, headers=headers)
        assert res.status_code == 200, res.text
    with pytest.raises(UnauthorizedException):
        await AuthService(db).refresh(tokens["refresh_token"])

    # A center that doesn't exist can't be linked.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.put(f"/api/v1/users/{manager['_id']}", json={"service_center_id": str(ObjectId())}, headers=headers)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_a_legacy_refresh_token_without_a_jti_is_honoured_only_once(db, manager):
    """Tokens minted before rotation carry no jti/fam. One may still be
    swapped once (its successor joins a fresh chain) — not replayed to mint
    new chains forever."""
    from app.core.security import TokenType, create_token

    auth = AuthService(db)
    legacy = create_token(str(manager["_id"]), manager["role"], TokenType.REFRESH, {"tv": manager.get("token_version", 0)})
    assert "jti" not in decode_token(legacy)
    successor = (await auth.refresh(legacy))["refresh_token"]
    assert decode_token(successor)["jti"] and decode_token(successor)["fam"]
    # A second tab within the grace window is fine…
    assert (await auth.refresh(legacy))["access_token"]
    # …a replay after it is refused.
    await db.spent_refresh_tokens.update_many(
        {"user_id": str(manager["_id"]), "_id": {"$regex": "^legacy:"}},
        {"$set": {"spent_at": datetime.now(timezone.utc) - timedelta(minutes=5)}},
    )
    with pytest.raises(UnauthorizedException):
        await auth.refresh(legacy)
    # The legitimately rotated successor keeps working.
    assert (await auth.refresh(successor))["access_token"]
