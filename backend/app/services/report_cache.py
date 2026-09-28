"""
Short-lived in-process cache for admin/manager REPORT endpoints (KPI
sections, dashboard summaries, the WhatsApp badge) — numbers where a
minute of staleness is fine but recomputing an aggregation for every
open tab, every poll, on every Cloud Run instance is not.

Single-flight: concurrent requests for the same key share one computation
instead of each running the aggregation. Per-process by design (each
instance keeps its own copy); nothing here is ever a source of truth.
Routes wrap service calls with it — services stay uncached so tests and
writes always see fresh data.
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Hashable

_MAX_ENTRIES = 512
_entries: dict[Hashable, tuple[float, Any]] = {}
_inflight: dict[Hashable, asyncio.Future] = {}


def _evict_expired_or_oldest() -> None:
    now = time.monotonic()
    for key in [k for k, (exp, _v) in _entries.items() if exp <= now]:
        _entries.pop(key, None)
    while len(_entries) >= _MAX_ENTRIES:
        _entries.pop(next(iter(_entries)))


async def cached(key: Hashable, ttl_seconds: float, compute: Callable[[], Awaitable[Any]]) -> Any:
    hit = _entries.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(compute())
        _inflight[key] = task

        def _store(done: asyncio.Future) -> None:
            _inflight.pop(key, None)
            if not done.cancelled() and done.exception() is None:
                _evict_expired_or_oldest()
                _entries[key] = (time.monotonic() + ttl_seconds, done.result())

        task.add_done_callback(_store)
    # shield: one caller disconnecting must not cancel the shared computation.
    return await asyncio.shield(task)


def invalidate(namespace: str) -> None:
    """Drops every entry whose key starts with `namespace` (keys are tuples
    whose first element is the namespace)."""
    for key in [k for k in _entries if isinstance(k, tuple) and k and k[0] == namespace]:
        _entries.pop(key, None)
