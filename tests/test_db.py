"""Tests for kessler.db: SQLite storage layer."""

from datetime import datetime, timezone

import pytest

from kessler.db import TLERecord, get_connection, upsert_records


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    yield connection
    connection.close()


def _record(norad_id: int, epoch: datetime, name: str = "TEST SAT") -> TLERecord:
    return TLERecord(
        name=name,
        norad_id=norad_id,
        line1=f"1 {norad_id:05d}U 24001A   24045.12345678  .00000456  00000-0  12345-4 0  9995",
        line2=f"2 {norad_id:05d}  51.6000 100.0000 0001000 100.0000 200.0000 15.00000000123456",
        epoch_utc=epoch,
    )


def test_upsert_inserts_new_record(conn) -> None:
    record = _record(99999, datetime(2024, 1, 29, tzinfo=timezone.utc))

    summary = upsert_records(conn, [record], fetched_at=datetime.now(timezone.utc))

    assert summary == {"inserted": 1, "updated": 0, "skipped": 0}
    row = conn.execute(
        "SELECT norad_id, name FROM satellites WHERE norad_id = ?", (99999,)
    ).fetchone()
    assert row == (99999, "TEST SAT")


def test_upsert_duplicate_norad_id_keeps_newest_epoch(conn) -> None:
    older = _record(99999, datetime(2024, 1, 1, tzinfo=timezone.utc), name="OLDER")
    newer = _record(99999, datetime(2024, 6, 1, tzinfo=timezone.utc), name="NEWER")

    upsert_records(conn, [older], fetched_at=datetime.now(timezone.utc))
    summary = upsert_records(conn, [newer], fetched_at=datetime.now(timezone.utc))

    assert summary == {"inserted": 0, "updated": 1, "skipped": 0}
    row = conn.execute("SELECT name FROM satellites WHERE norad_id = ?", (99999,)).fetchone()
    assert row[0] == "NEWER"


def test_upsert_older_epoch_does_not_replace_newer(conn) -> None:
    newer = _record(99999, datetime(2024, 6, 1, tzinfo=timezone.utc), name="NEWER")
    older = _record(99999, datetime(2024, 1, 1, tzinfo=timezone.utc), name="STALE")

    upsert_records(conn, [newer], fetched_at=datetime.now(timezone.utc))
    summary = upsert_records(conn, [older], fetched_at=datetime.now(timezone.utc))

    assert summary == {"inserted": 0, "updated": 0, "skipped": 1}
    row = conn.execute("SELECT name FROM satellites WHERE norad_id = ?", (99999,)).fetchone()
    assert row[0] == "NEWER"


def test_get_connection_creates_schema(tmp_path) -> None:
    db_path = tmp_path / "fresh.db"

    conn = get_connection(str(db_path))
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'satellites'"
        ).fetchall()
    finally:
        conn.close()

    assert db_path.exists()
    assert tables == [("satellites",)]
