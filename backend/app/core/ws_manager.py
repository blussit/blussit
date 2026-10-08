"""
WebSocket connection registry, keyed by "channel" string — across every
Cloud Run instance (PERF-01).

Each instance keeps its own sockets in memory. A broadcast is published to
the small `ws_events` collection, and every instance tails that collection
with a MongoDB change stream (`run_relay`) and delivers each event to the
sockets IT holds — so a customer connected to instance B hears about a
booking changed on instance A. Events expire after a few minutes (TTL
index). If the relay isn't running (tests, a database without change
streams, a relay outage) a broadcast falls back to this instance's sockets
only — the pre-PERF-01 behaviour, never an error.

Every message pushed through here is a small "something changed, refetch
it" ping, never the authoritative payload itself (the one exception is
captain-location pings, which are cheap and safe to push directly — see
BookingService.update_captain_location) — callers still fetch the real
data over the normal authenticated REST endpoints. This keeps exactly one
source of truth for response shape/authorization (the REST layer), with
the socket only responsible for "wake up and go fetch," which is also
what makes this safe to add without duplicating every endpoint's
authorization logic over the socket too.
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# How long a published event lives (TTL index on ws_events.at — see
# database.create_indexes). Instances deliver within milliseconds; this only
# bounds the collection.
WS_EVENT_TTL_SECONDS = 300
_RELAY_RETRY_SECONDS = 5


class ConnectionManager:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}
        self._db = None
        self._relay_live = False

    def subscribe(self, channel: str, websocket: WebSocket) -> None:
        self._channels.setdefault(channel, set()).add(websocket)

    def unsubscribe(self, channel: str, websocket: WebSocket) -> None:
        sockets = self._channels.get(channel)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._channels.pop(channel, None)

    def unsubscribe_all(self, websocket: WebSocket) -> None:
        """Called on disconnect — a client may be subscribed to several
        channels at once (e.g. a manager watching both a booking and their
        center's queue), and nothing else tracks that set for it."""
        for channel in list(self._channels.keys()):
            self.unsubscribe(channel, websocket)

    async def broadcast(self, channel: str, message: dict) -> None:
        """Every instance's subscribers to `channel` get `message`: published
        through ws_events while the relay is live (each instance, this one
        included, delivers it from its change stream), else delivered to
        this instance's sockets directly. Never raises."""
        if self._relay_live and self._db is not None:
            try:
                await self._db.ws_events.insert_one({"channel": channel, "message": message, "at": datetime.now(timezone.utc)})
                return
            except Exception:  # noqa: BLE001 — degrade to this instance, never fail the caller
                logger.warning("WebSocket relay publish failed — delivering on this instance only", exc_info=True)
        await self._deliver_local(channel, message)

    async def _deliver_local(self, channel: str, message: dict) -> None:
        """Best-effort — a dead/closing socket that fails to send is
        dropped from the registry rather than raising, so one stale
        connection can never take down a broadcast for everyone else."""
        sockets = list(self._channels.get(channel, ()))
        if not sockets:
            return
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(channel, ws)

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))

    @property
    def relay_live(self) -> bool:
        return self._relay_live

    async def run_relay(self, db, stopping=lambda: False) -> None:
        """Tail ws_events (inserts only) and deliver each event to this
        instance's sockets. Runs for the life of the process (main.py
        starts it and cancels it on shutdown); a broken stream reconnects
        after a short pause, and broadcasts go local-only meanwhile."""
        self._db = db
        while not stopping():
            try:
                async with db.ws_events.watch([{"$match": {"operationType": "insert"}}]) as stream:
                    self._relay_live = True
                    async for change in stream:
                        doc = change.get("fullDocument") or {}
                        if doc.get("channel"):
                            await self._deliver_local(doc["channel"], doc.get("message") or {})
                        if stopping():
                            break
            except asyncio.CancelledError:
                self._relay_live = False
                raise
            except Exception:  # noqa: BLE001
                logger.warning("WebSocket relay stream failed — local-only delivery until it reconnects", exc_info=True)
            self._relay_live = False
            if not stopping():
                await asyncio.sleep(_RELAY_RETRY_SECONDS)
        self._relay_live = False


manager = ConnectionManager()
