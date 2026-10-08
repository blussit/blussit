"""
Captain job list scopes + WebSocket auth close code.

The captain app used to fetch page 1 of 100 jobs of ANY status, oldest
first, and keep the active ones in the browser — so after ~100 finished
jobs his Active tab (and the GPS pings gated on it) went blank. The list is
now scoped on the server: active = still to do, soonest first; history =
finished, newest first, paginated.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from starlette.websockets import WebSocketDisconnect

from app.controllers.booking_controller import BookingController
from app.core.config import settings
from app.core.dependencies import CurrentUser, PaginationParams
from app.routes.v1 import ws_routes

MARK = {"_captain_jobs_scope": True}


def _pg(page: int = 1, page_size: int = 100) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size, search=None, sort_by="created_at", sort_order=-1)


@pytest.fixture
async def rig(db):
    await db.bookings.delete_many(MARK)
    captain_id = str(ObjectId())
    other_captain = str(ObjectId())
    base = datetime(2026, 1, 1, 9, 0)
    docs = []
    # 130 finished jobs, all scheduled BEFORE the active ones — exactly the
    # shape that pushed every active job off the old oldest-first page 1.
    for i in range(130):
        docs.append({
            **MARK, "booking_number": f"BKCJS{i:04d}", "captain_id": captain_id,
            "customer_id": str(ObjectId()), "address_id": str(ObjectId()), "service_ids": [],
            "status": "cancelled" if i % 10 == 0 else "completed", "payment_status": "paid",
            "scheduled_date": base + timedelta(days=i), "scheduled_slot": "09:00-12:00",
            "total_amount": 499, "platform_earning": 350, "is_deleted": False,
        })
    active_specs = [
        ("assigned", base + timedelta(days=200), "15:00-18:00"),
        ("service_started", base + timedelta(days=199), "09:00-12:00"),
        ("captain_on_the_way", base + timedelta(days=200), "09:00-12:00"),
    ]
    for n, (status, when, slot) in enumerate(active_specs):
        docs.append({
            **MARK, "booking_number": f"BKCJSA{n}", "captain_id": captain_id,
            "customer_id": str(ObjectId()), "address_id": str(ObjectId()), "service_ids": [],
            "status": status, "payment_status": "pending", "scheduled_date": when, "scheduled_slot": slot,
            "total_amount": 499, "platform_earning": 350, "is_deleted": False,
        })
    # Someone else's active job and a deleted one of ours must never leak in.
    docs.append({**MARK, "booking_number": "BKCJSX1", "captain_id": other_captain, "customer_id": str(ObjectId()),
                 "address_id": str(ObjectId()), "service_ids": [], "status": "assigned",
                 "scheduled_date": base, "scheduled_slot": "09:00-12:00", "total_amount": 1, "is_deleted": False})
    docs.append({**MARK, "booking_number": "BKCJSX2", "captain_id": captain_id, "customer_id": str(ObjectId()),
                 "address_id": str(ObjectId()), "service_ids": [], "status": "assigned",
                 "scheduled_date": base, "scheduled_slot": "09:00-12:00", "total_amount": 1, "is_deleted": True})
    await db.bookings.insert_many(docs)
    yield CurrentUser(id=captain_id, role="captain")
    await db.bookings.delete_many(MARK)


async def test_active_scope_survives_a_long_history(db, rig):
    ctrl = BookingController(db)
    for scope in ("active", None):  # no scope + no status (old clients) also means active
        res = await ctrl.list_my_jobs(rig, None, _pg(), scope)
        assert res["meta"]["total"] == 3
        assert [b["booking_number"] for b in res["data"]] == ["BKCJSA1", "BKCJSA2", "BKCJSA0"]  # soonest first
        assert all("platform_earning" not in b for b in res["data"])  # still redacted for the captain


async def test_active_scope_small_page_answers_has_active_job(db, rig):
    res = await BookingController(db).list_my_jobs(rig, None, _pg(1, 1), "active")
    assert res["meta"]["total"] == 3 and len(res["data"]) == 1


async def test_history_scope_newest_first_and_paginated(db, rig):
    ctrl = BookingController(db)
    page1 = await ctrl.list_my_jobs(rig, None, _pg(1, 20), "history")
    page7 = await ctrl.list_my_jobs(rig, None, _pg(7, 20), "history")
    assert page1["meta"]["total"] == 130 and page1["meta"]["total_pages"] == 7
    assert page1["data"][0]["booking_number"] == "BKCJS0129"
    assert page1["data"][-1]["booking_number"] == "BKCJS0110"
    assert [b["booking_number"] for b in page7["data"]] == [f"BKCJS{i:04d}" for i in range(9, -1, -1)]

    completed = await ctrl.list_my_jobs(rig, "completed", _pg(1, 20), "history")
    assert completed["meta"]["total"] == 117
    assert all(b["status"] == "completed" for b in completed["data"])
    assert completed["data"][0]["booking_number"] == "BKCJS0129"

    # Old clients send status=completed with no scope — same newest-first answer.
    legacy = await ctrl.list_my_jobs(rig, "completed", _pg(1, 20), None)
    assert [b["id"] for b in legacy["data"]] == [b["id"] for b in completed["data"]]


async def test_status_outside_scope_is_empty_and_status_narrows(db, rig):
    ctrl = BookingController(db)
    assert (await ctrl.list_my_jobs(rig, "completed", _pg(), "active"))["meta"]["total"] == 0
    assert (await ctrl.list_my_jobs(rig, "assigned", _pg(), "history"))["meta"]["total"] == 0
    only = await ctrl.list_my_jobs(rig, "service_started", _pg(), "active")
    assert [b["booking_number"] for b in only["data"]] == ["BKCJSA1"]
    everything = await ctrl.list_my_jobs(rig, None, _pg(1, 5), "all")
    assert everything["meta"]["total"] == 133


def test_my_jobs_rejects_unknown_scope():
    from app.core.dependencies import get_current_user, get_db
    from app.routes.v1 import booking_routes

    app = FastAPI()
    app.include_router(booking_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=str(ObjectId()), role="captain")
    app.dependency_overrides[get_db] = lambda: None
    res = TestClient(app).get("/api/v1/bookings/my-jobs", params={"scope": "everything"})
    assert res.status_code == 422


def _ws_app(monkeypatch) -> TestClient:
    monkeypatch.setattr(ws_routes, "get_database", lambda: object())

    # The socket resolves its user from the DB (ws_routes._ws_user ->
    # resolve_access_token); stand in an always-active account so these
    # tests stay about token decoding / expiry, not account standing.
    async def _active_user(token: str):
        from app.core.dependencies import CurrentUser
        from app.core.exceptions import UnauthorizedException
        from app.core.security import decode_token

        try:
            payload = decode_token(token)
        except ValueError as exc:
            raise UnauthorizedException(str(exc)) from exc
        return CurrentUser(id=payload["sub"], role=payload.get("role", "customer"))

    monkeypatch.setattr(ws_routes, "resolve_access_token", _active_user)
    app = FastAPI()
    app.include_router(ws_routes.router, prefix="/api/v1")
    return TestClient(app)


def _token(seconds: float) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(ObjectId()), "role": "customer", "type": "access", "iat": now, "exp": now + timedelta(seconds=seconds)},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def test_ws_bad_token_is_accepted_then_closed_4401(monkeypatch):
    # Closing before accept reaches a browser as a bare 1006 — the client
    # could never tell "refresh your token" from a network blip.
    client = _ws_app(monkeypatch)
    for token in ("garbage", _token(-60)):
        with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4401


def test_ws_closes_4401_at_token_expiry(monkeypatch):
    """The socket schedules its own close at the token's `exp`.

    Deterministic: the token is valid for 10 minutes (so the handshake can
    never race its expiry — JWT `exp` is whole seconds, so a "1.5 s" token
    used to have as little as 0.5 s left, and a loaded test run sometimes
    saw it expire before "connected"), while the socket's clock is pinned to
    half a second before that `exp` — the server must still close it, with
    4401, once the token runs out."""
    client = _ws_app(monkeypatch)
    token = _token(600)
    exp = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])["exp"]

    class _NearExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(exp - 0.5, tz)

    monkeypatch.setattr(ws_routes, "datetime", _NearExpiry)
    with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
        assert ws.receive_json() == {"type": "connected"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401


def test_ws_stays_open_while_the_token_is_valid(monkeypatch):
    """The other half: a socket whose token has plenty of time left is not
    closed early — it keeps answering."""
    client = _ws_app(monkeypatch)
    with client.websocket_connect(f"/api/v1/ws?token={_token(600)}") as ws:
        assert ws.receive_json() == {"type": "connected"}
        ws.send_json({"action": "ping", "channel": "x"})
        assert ws.receive_json() == {"type": "error", "message": "Unknown action: ping"}


def test_ws_caps_channels_per_socket(monkeypatch):
    client = _ws_app(monkeypatch)
    with client.websocket_connect(f"/api/v1/ws?token={_token(600)}") as ws:
        assert ws.receive_json() == {"type": "connected"}
        for i in range(ws_routes.MAX_CHANNELS_PER_SOCKET):
            ws.send_json({"action": "subscribe", "channel": f"slots:c{i}:2026-01-01"})
            assert ws.receive_json()["type"] == "subscribed"
        ws.send_json({"action": "subscribe", "channel": "slots:one-too-many:2026-01-01"})
        assert ws.receive_json() == {"type": "error", "message": "Too many channels on this connection"}
