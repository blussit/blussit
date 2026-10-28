"""Red-team 2 (2026-10-28): websocket resource-abuse hardening.
- `slots:*` was open to ANY channel name (a signed-in client could register
  unbounded junk channel names in the server's memory): it now accepts only
  `slots:<24-hex center id>:<YYYY-MM-DD>` — the one shape the booking page uses.
- One account can hold only a bounded number of sockets per instance.
- uvicorn is started with a small websocket frame cap."""
import re
from pathlib import Path

import pytest

from app.routes.v1 import ws_routes
from app.routes.v1.ws_routes import _WsUser, _authorize_channel

pytestmark = pytest.mark.asyncio

CENTER = "6ae1d2633fbb6dca68c64419"
USER = _WsUser(id="6ae1d2633fbb6dca68c64428", role="customer", service_center_id=None)


@pytest.mark.parametrize("channel", [f"slots:{CENTER}:2026-11-02"])
async def test_real_slot_channel_still_allowed(db, channel):
    assert await _authorize_channel(channel, USER, db) is True


@pytest.mark.parametrize(
    "channel",
    ["slots:x:y", "slots:a:b", f"slots:{CENTER}:tomorrow", f"slots:{CENTER}:2026-11-02x", "slots:*:*", f"slots:{CENTER}:", "slots::", f"slots:{CENTER[:-1]}:2026-11-02"],
)
async def test_junk_slot_channels_are_refused(db, channel):
    assert await _authorize_channel(channel, USER, db) is False


async def test_per_user_socket_cap():
    uid = "u-cap-test"
    cap = ws_routes.MAX_SOCKETS_PER_USER
    try:
        assert all(ws_routes._reserve_socket(uid) for _ in range(cap))
        assert ws_routes._reserve_socket(uid) is False  # one more than allowed
        ws_routes._release_socket(uid)
        assert ws_routes._reserve_socket(uid) is True  # a freed slot is reusable
        assert ws_routes._reserve_socket("another-user") is True  # other accounts unaffected
    finally:
        for _ in range(cap + 2):
            ws_routes._release_socket(uid)
        ws_routes._release_socket("another-user")
    assert uid not in ws_routes._open_sockets and "another-user" not in ws_routes._open_sockets


def test_uvicorn_starts_with_a_websocket_frame_cap():
    cmd = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    m = re.search(r"--ws-max-size\s+(\d+)", cmd)
    assert m and int(m.group(1)) <= 1_048_576
