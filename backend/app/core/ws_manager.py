"""
In-process WebSocket connection registry, keyed by "channel" string.

This app runs as a single FastAPI process with no Redis/Celery (see
main.py's `_reminder_loop` docstring — that's an explicit, existing
architectural choice, not an oversight) — an in-memory registry is
therefore the correct scope for this: it works today, and the moment this
app is ever run behind multiple worker processes, this would need to move
to a pub/sub broker (Redis) instead, exactly like the reminder loop would.
Documented here so that migration point is obvious later, not silently
broken.

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
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}

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


manager = ConnectionManager()
