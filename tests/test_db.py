"""Tests for kessler.db."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kessler.db import SatelliteRecord, get_connection, upsert_records


@pytest.fixture
def conn():
    connection = get_connection(":memory:")
    yield connection
    connection.close()


def _record(norad_id: int, epoch: datetime, name: str = "SAT") -> SatelliteRecord:
    return SatelliteRecord(
        name=name,
        norad_id=norad_id,
        line1=f"1 {norad_id:05d}U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927",
        line2=f"2 {norad_id:05d}  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537",
        epoch_utc=epoch,
    )


def test_upsert_new_record_is_inserted(conn):
    record = _record(25544, datetime(2008, 9, 20, tzinfo=UTC))

    result = upsert_records(conn, [record])

    assert result.inserted == 1
    assert result.updated == 0
    assert result.skipped == 0

    row = conn.execute(
        "SELECT norad_id, name FROM satellites WHERE norad_id = ?", (25544,)
    ).fetchone()
    assert row == (25544, "SAT")


def test_upsert_newer_epoch_replaces_older(conn):
    older = _record(25544, datetime(2008, 9, 20, tzinfo=UTC))
    newer = _record(25544, datetime(2008, 9, 21, tzinfo=UTC), name="SAT NEW")

    first = upsert_records(conn, [older])
    second = upsert_records(conn, [newer])

    assert first.inserted == 1
    assert second.updated == 1
    assert second.inserted == 0
    assert second.skipped == 0

    row = conn.execute("SELECT name FROM satellites WHERE norad_id = ?", (25544,)).fetchone()
    assert row == ("SAT NEW",)


def test_upsert_older_epoch_does_not_regress(conn):
    newer = _record(25544, datetime(2008, 9, 21, tzinfo=UTC), name="SAT NEW")
    older = _record(25544, datetime(2008, 9, 20, tzinfo=UTC), name="SAT OLD")

    upsert_records(conn, [newer])
    second = upsert_records(conn, [older])

    assert second.inserted == 0
    assert second.updated == 0
    assert second.skipped == 1

    row = conn.execute("SELECT name FROM satellites WHERE norad_id = ?", (25544,)).fetchone()
    assert row == ("SAT NEW",)


def test_upsert_same_epoch_counts_as_skipped(conn):
    record = _record(25544, datetime(2008, 9, 20, tzinfo=UTC))

    first = upsert_records(conn, [record])
    second = upsert_records(conn, [record])

    assert first.inserted == 1
    assert first.updated == 0
    assert first.skipped == 0

    assert second.inserted == 0
    assert second.updated == 0
    assert second.skipped == 1

    count = conn.execute("SELECT COUNT(*) FROM satellites WHERE norad_id = ?", (25544,)).fetchone()[
        0
    ]
    assert count == 1
