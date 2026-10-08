"""PERF-01 — websocket events cross instances. Two ConnectionManagers stand
in for two Cloud Run instances sharing one database: a booking changed on
instance A reaches a socket connected to instance B (through the ws_events
change stream). Without a running relay a broadcast still reaches local
sockets, exactly as before."""
import asyncio

import pytest

from app.core.ws_manager import ConnectionManager


class FakeSocket:
    def __init__(self):
        self.received: list[dict] = []

    async def send_json(self, message):
        self.received.append(message)


async def _wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


@pytest.mark.asyncio
async def test_broadcast_on_one_instance_reaches_sockets_on_another(db):
    a, b = ConnectionManager(), ConnectionManager()
    on_a, on_b, other = FakeSocket(), FakeSocket(), FakeSocket()
    a.subscribe("booking:42", on_a)
    b.subscribe("booking:42", on_b)
    b.subscribe("booking:99", other)
    relays = [asyncio.create_task(a.run_relay(db)), asyncio.create_task(b.run_relay(db))]
    try:
        assert await _wait_for(lambda: a.relay_live and b.relay_live), "both relays came up"
        await a.broadcast("booking:42", {"type": "booking_updated", "id": "42"})
        assert await _wait_for(lambda: on_a.received and on_b.received)
        assert on_a.received == [{"type": "booking_updated", "id": "42"}], "delivered once on the sender's instance"
        assert on_b.received == [{"type": "booking_updated", "id": "42"}], "and once on the other instance"
        await asyncio.sleep(0.3)
        assert other.received == [], "other channels hear nothing"
    finally:
        for t in relays:
            t.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
        await db.ws_events.delete_many({})
    assert not a.relay_live and not b.relay_live


@pytest.mark.asyncio
async def test_without_a_relay_broadcast_stays_local():
    m = ConnectionManager()
    sock = FakeSocket()
    m.subscribe("user:1", sock)
    await m.broadcast("user:1", {"ping": 1})
    assert sock.received == [{"ping": 1}]


@pytest.mark.asyncio
async def test_a_dead_socket_never_breaks_a_broadcast():
    class Dead(FakeSocket):
        async def send_json(self, message):
            raise RuntimeError("closed")

    m = ConnectionManager()
    alive, dead = FakeSocket(), Dead()
    m.subscribe("c", alive)
    m.subscribe("c", dead)
    await m.broadcast("c", {"x": 1})
    assert alive.received == [{"x": 1}] and m.subscriber_count("c") == 1
