"""
One pooled outbound HTTP client per integration (Meta WhatsApp, Google
Routes, R2 storage, MSG91) instead of a fresh httpx.AsyncClient per call —
each new client meant a new TLS handshake plus a CA-bundle load on the
event loop, on every WhatsApp message and photo upload.

Created lazily on first use and closed by the app's shutdown hook.
"""
import asyncio

import httpx

_clients: dict[str, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}


def shared_client(name: str, timeout: float) -> httpx.AsyncClient:
    """The pooled client for `name`. `timeout` is its default read/write
    budget; a call that needs longer passes its own `timeout=`."""
    loop = asyncio.get_running_loop()
    entry = _clients.get(name)
    if entry is not None:
        owner, client = entry
        # A client's pooled connections belong to the loop that opened them;
        # the isinstance check lets a test that swaps httpx.AsyncClient for
        # a stand-in actually get its stand-in.
        if owner is loop and isinstance(client, httpx.AsyncClient) and not getattr(client, "is_closed", False):
            return client
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
    )
    _clients[name] = (loop, client)
    return client


async def close_shared_clients() -> None:
    entries = list(_clients.values())
    _clients.clear()
    loop = asyncio.get_running_loop()
    for owner, client in entries:
        if owner is not loop:
            continue
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — shutting down anyway
            pass
