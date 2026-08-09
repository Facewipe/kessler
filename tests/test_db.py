"""Tests for kessler.db."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from kessler.db import CURRENT_SCHEMA_VERSION, SatelliteRecord, get_connection, upsert_records


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


def _create_legacy_db(db_path) -> None:
    """Create a `satellites` table as it existed before epoch_utc/fetched_at."""
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute(
        "CREATE TABLE satellites ("
        "norad_id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "line1 TEXT NOT NULL, line2 TEXT NOT NULL)"
    )
    legacy_conn.execute(
        "INSERT INTO satellites (norad_id, name, line1, line2) VALUES (?, ?, ?, ?)",
        (25544, "ISS (ZARYA)", "1 LEGACY LINE ONE", "2 LEGACY LINE TWO"),
    )
    legacy_conn.commit()
    legacy_conn.close()


def test_get_connection_migrates_legacy_schema_missing_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT norad_id, name, line1, line2, epoch_utc, fetched_at "
            "FROM satellites WHERE norad_id = ?",
            (25544,),
        ).fetchone()
    finally:
        conn.close()

    assert row == (25544, "ISS (ZARYA)", "1 LEGACY LINE ONE", "2 LEGACY LINE TWO", None, None)


def test_get_connection_migration_lets_upsert_use_new_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)

    conn = get_connection(db_path)
    try:
        record = SatelliteRecord(
            norad_id=25544,
            name="ISS (ZARYA)",
            line1="1 NEW LINE ONE",
            line2="2 NEW LINE TWO",
            epoch_utc=datetime(2024, 1, 1, tzinfo=UTC),
        )
        # Does not raise "no such column: epoch_utc" (the bug this migration fixes).
        result = upsert_records(conn, [record])
        assert result.inserted + result.updated == 1

        row = conn.execute(
            "SELECT epoch_utc FROM satellites WHERE norad_id = ?", (25544,)
        ).fetchone()
        assert row[0] == "2024-01-01T00:00:00+00:00"
    finally:
        conn.close()


def test_get_connection_sets_schema_version_and_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)

    first = get_connection(db_path)
    version = first.execute("PRAGMA user_version").fetchone()[0]
    first.close()
    assert version == CURRENT_SCHEMA_VERSION

    # Reopening an already-migrated database must not re-migrate or error.
    second = get_connection(db_path)
    try:
        row = second.execute(
            "SELECT norad_id, name FROM satellites WHERE norad_id = ?", (25544,)
        ).fetchone()
    finally:
        second.close()
    assert row == (25544, "ISS (ZARYA)")


def test_get_connection_on_fresh_db_creates_current_schema(tmp_path):
    db_path = tmp_path / "fresh.db"

    conn = get_connection(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(satellites)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    assert columns == {"norad_id", "name", "line1", "line2", "epoch_utc", "fetched_at"}
    assert version == CURRENT_SCHEMA_VERSION
