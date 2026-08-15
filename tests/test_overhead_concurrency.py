"""Test that a heavy /overhead call does not block the event loop.

Same production failure as /conjunctions (see test_screening_concurrency.py)
via a different path: /overhead propagates the *entire* catalog on every
call (nothing about a satellite's current position can be pruned ahead of
time), so a large catalog alone -- no pathological overlap needed -- was
enough to saturate a single shared vCPU for long enough to make health
checks fail ~20s later, per the reported incident.

Verified directly against the event loop (a concurrent heartbeat coroutine
that must keep ticking throughout the call), same style as
test_screening_concurrency.py and for the same reason: HTTP-layer
concurrency between two client requests isn't guaranteed enough to reliably
distinguish "blocked" from "not blocked".

No pytest-asyncio dependency: the `async def` body is driven directly via
`asyncio.run()` from an ordinary, synchronous test function.
"""

from __future__ import annotations

import asyncio

import pytest

import kessler.api as kessler_api
from kessler.propagate import epoch_datetime, satrec_from_tle

from .conftest import TEST_TLE_LINE1, TEST_TLE_LINE2

# Heartbeat cadence while the call runs. If the event loop is blocked, the
# heartbeat cannot tick at all (single-threaded cooperative scheduling); if
# the computation is properly offloaded, it should tick roughly every
# interval.
_HEARTBEAT_INTERVAL_SECONDS = 0.01
_MIN_EXPECTED_TICKS = 10

# All sharing one TLE means every entry is at the same position, so none of
# them are pruned by the coarse ground-track filter -- every one needs the
# full topocentric conversion, which is the actual production failure mode
# (nothing about /overhead's per-satellite work can be skipped ahead of
# time). Empirically ~640ms for 60000 at this observer/elevation, long
# enough for a blocked event loop to visibly starve the heartbeat below.
_SYNTHETIC_CATALOG_SIZE = 60000


@pytest.mark.slow
def test_overhead_call_does_not_block_event_loop(db_conn) -> None:
    # Bulk insert, bypassing upsert_satellite/upsert_records (which commit
    # or SELECT per row): this is throwaway test-catalog setup, not
    # something the endpoint itself does, and 60000 individual commits would
    # make the test itself the slow part.
    epoch = epoch_datetime(satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)).isoformat()
    db_conn.executemany(
        "INSERT OR REPLACE INTO satellites (norad_id, name, line1, line2, epoch_utc, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (500000 + i, f"SAT-{i}", TEST_TLE_LINE1, TEST_TLE_LINE2, epoch, epoch)
            for i in range(_SYNTHETIC_CATALOG_SIZE)
        ],
    )
    db_conn.commit()

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
    response = await kessler_api.get_overhead(
        lat=51.5074, lon=-0.1278, min_elevation_deg=10.0, alt_m=0.0, conn=conn
    )
    ticks_during = ticks - ticks_before

    stop = True
    await heartbeat_task

    assert response["truncated"] is False
    assert ticks_during >= _MIN_EXPECTED_TICKS
