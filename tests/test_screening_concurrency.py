"""Test that a heavy /conjunctions screen does not block the event loop.

This is the actual production failure being fixed: before screening was
offloaded to a worker thread, `GET /conjunctions/{norad_id}` ran its whole
screen synchronously inside the request coroutine, monopolizing the single
event loop thread for the screen's entire duration and starving every other
in-flight request -- including health checks -- which is what made the
whole machine look unresponsive to Fly.io.

Verified directly against the event loop (a concurrent heartbeat coroutine
that must keep ticking throughout the screen) rather than through the HTTP
layer, since asyncio/ASGI transport scheduling order between two
concurrently-created client requests isn't guaranteed enough to reliably
distinguish "blocked" from "not blocked" through that layer.

No pytest-asyncio dependency: the `async def` body is driven directly via
`asyncio.run()` from an ordinary, synchronous test function.
"""

from __future__ import annotations

import asyncio

import pytest

import kessler.api as kessler_api
from kessler.db import SatelliteRecord, upsert_satellite

from .conftest import TEST_NORAD_ID
from .test_screen import CLOSE_NORAD_ID, CLOSE_TLE_LINE1, CLOSE_TLE_LINE2

# Heartbeat cadence while the screen runs. If the event loop is blocked, the
# heartbeat cannot tick at all (single-threaded cooperative scheduling); if
# screening is properly offloaded, it should tick roughly every interval.
_HEARTBEAT_INTERVAL_SECONDS = 0.01
_MIN_EXPECTED_TICKS = 10


@pytest.mark.slow
def test_conjunctions_screen_does_not_block_event_loop(db_conn) -> None:
    # Many entries sharing the target's orbit: none pruned by the coarse
    # filter, so this screen genuinely takes a while (empirically ~750ms for
    # 20 such pairs over a 72h window) -- long enough for a blocked event
    # loop to visibly starve the heartbeat below.
    for i in range(20):
        upsert_satellite(
            db_conn,
            SatelliteRecord(
                norad_id=CLOSE_NORAD_ID + 100 + i,
                name=f"OVERLAP-{i}",
                line1=CLOSE_TLE_LINE1,
                line2=CLOSE_TLE_LINE2,
            ),
        )

    asyncio.run(_assert_loop_stays_responsive(db_conn))


async def _assert_loop_stays_responsive(conn) -> None:
    ticks = 0
    stop = False

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0)  # let the heartbeat task start ticking

    ticks_before = ticks
    response = await kessler_api.get_conjunctions(
        norad_id=TEST_NORAD_ID, hours=72, threshold_km=50.0, min_separation_km=1.0, conn=conn
    )
    ticks_during = ticks - ticks_before

    stop = True
    await heartbeat_task

    assert response["truncated"] is False
    assert ticks_during >= _MIN_EXPECTED_TICKS
