"""
Real-time push channel — replaces polling for slot availability, booking
status, manager queues, and captain location with an authenticated
WebSocket the frontend subscribes to. See app/core/ws_manager.py for the
in-process design constraint this accepts (single-process deployment,
same as the existing in-process reminder loop).

Every message THIS SERVER sends is one of:
  {"type": "connected"}
  {"type": "subscribed", "channel": "..."}
  {"type": "unsubscribed", "channel": "..."}
  {"type": "error", "message": "..."}
  {"type": "changed", "channel": "...", ...small context...}   — "go refetch"
  {"type": "captain_location", "channel": "...", "latitude", "longitude", "captured_at"}  — pushed directly, cheap+safe

The browser WebSocket API can't set an Authorization header, so the JWT is
passed as a query param instead (?token=...) — same access token already
used everywhere else, just carried differently for this one connection
type. A token that fails to decode closes the connection immediately with
code 4401, before any channel is ever accepted.
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_database
from app.core.security import decode_token
from app.core.ws_manager import manager
from app.models.enums import UserRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Realtime"])


class _WsUser:
    __slots__ = ("id", "role", "service_center_id")

    def __init__(self, id: str, role: str, service_center_id: str | None):
        self.id = id
        self.role = role
        self.service_center_id = service_center_id


async def _authorize_channel(channel: str, user: _WsUser, db: AsyncIOMotorDatabase) -> bool:
    """Mirrors the REST layer's own authorization for the same data —
    deliberately re-checked here rather than trusted from the client, same
    "never trust the client" discipline as every other authorization check
    in this codebase (spec Section 28)."""
    parts = channel.split(":")
    kind = parts[0]

    if kind == "slots" and len(parts) == 3:
        return True  # same as the public GET /service-centers/{id}/slots — no auth restriction on this data

    if kind == "center-bookings" and len(parts) == 2:
        center_id = parts[1]
        if user.role == "admin":
            return True
        return user.role == "manager" and user.service_center_id == center_id

    if kind == "user" and len(parts) == 2:
        return parts[1] == user.id  # only ever your own personal channel

    if kind == "booking" and len(parts) == 2:
        booking = await db.bookings.find_one({"_id": _safe_object_id(parts[1])})
        if not booking:
            return False
        if user.role == "admin":
            return True
        if user.role == "customer":
            return booking.get("customer_id") == user.id
        if user.role == "captain":
            return booking.get("captain_id") == user.id
        if user.role == "manager":
            return booking.get("service_center_id") == user.service_center_id
        return False

    if kind == "captain-location" and len(parts) == 2:
        captain_id = parts[1]
        if user.role == "admin" or user.id == captain_id:
            return True
        if user.role == "manager":
            captain = await db.users.find_one({"_id": _safe_object_id(captain_id)})
            return bool(captain and captain.get("service_center_id") == user.service_center_id)
        return False

    return False


def _safe_object_id(value: str):
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(value)
    except InvalidId:
        return None


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise ValueError("Invalid token type")
    except ValueError:
        await websocket.close(code=4401)
        return

    user = _WsUser(
        id=payload["sub"],
        role=payload.get("role", UserRole.CUSTOMER.value),
        service_center_id=payload.get("service_center_id"),
    )
    db = get_database()

    await websocket.accept()
    await websocket.send_json({"type": "connected"})
    subscribed: set[str] = set()

    # A socket must not outlive its token: without this, one handshake
    # stayed authorized forever — through token expiry, role changes, and
    # account suspension. Closing at `exp` forces a reconnect with a fresh
    # token (the frontend's socket client already auto-reconnects), which
    # re-runs decode_token and re-authorizes every channel.
    token_exp = payload.get("exp")
    expiry_handle = None
    if token_exp:
        delay = max(1.0, token_exp - datetime.now(timezone.utc).timestamp())

        async def _close_at_expiry() -> None:
            await asyncio.sleep(delay)
            try:
                await websocket.close(code=4401)
            except Exception:  # noqa: BLE001 — already closed is fine
                pass

        expiry_handle = asyncio.create_task(_close_at_expiry())

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            channel = data.get("channel")
            if not isinstance(channel, str) or not channel:
                await websocket.send_json({"type": "error", "message": "channel is required"})
                continue

            if action == "subscribe":
                if channel in subscribed:
                    continue
                if not await _authorize_channel(channel, user, db):
                    await websocket.send_json({"type": "error", "message": f"Not authorized for channel: {channel}"})
                    continue
                manager.subscribe(channel, websocket)
                subscribed.add(channel)
                await websocket.send_json({"type": "subscribed", "channel": channel})
            elif action == "unsubscribe":
                manager.unsubscribe(channel, websocket)
                subscribed.discard(channel)
                await websocket.send_json({"type": "unsubscribed", "channel": channel})
            else:
                await websocket.send_json({"type": "error", "message": f"Unknown action: {action}"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket connection error")
    finally:
        if expiry_handle:
            expiry_handle.cancel()
        manager.unsubscribe_all(websocket)
