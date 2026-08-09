"""Tests for kessler.db: schema creation and upsert semantics."""

from datetime import UTC, datetime

from kessler.db import TLERecord, get_connection, upsert_records


def _record(norad_id: int, epoch_utc: datetime, name: str = "TEST SAT") -> TLERecord:
    return TLERecord(
        name=name,
        norad_id=norad_id,
        line1=f"1 {norad_id:05d}U 98067A   24045.50000000 -.00002182  00000-0 -11606-4 0  2927",
        line2=f"2 {norad_id:05d}  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537",
        epoch_utc=epoch_utc,
    )


def test_insert_new_record() -> None:
    conn = get_connection(":memory:")
    record = _record(11111, datetime(2024, 1, 1, tzinfo=UTC))

    result = upsert_records(conn, [record])

    assert result.inserted == 1
    assert result.updated == 0
    assert result.skipped == 0
    row = conn.execute("SELECT name FROM tle WHERE norad_id = ?", (11111,)).fetchone()
    assert row[0] == "TEST SAT"


def test_upsert_keeps_newest_epoch() -> None:
    conn = get_connection(":memory:")
    older = _record(22222, datetime(2024, 1, 1, tzinfo=UTC), name="OLD")
    newer = _record(22222, datetime(2024, 2, 1, tzinfo=UTC), name="NEW")

    upsert_records(conn, [older])
    result = upsert_records(conn, [newer])

    assert result.updated == 1
    row = conn.execute("SELECT name FROM tle WHERE norad_id = ?", (22222,)).fetchone()
    assert row[0] == "NEW"


def test_upsert_does_not_regress_to_older_epoch() -> None:
    conn = get_connection(":memory:")
    newer = _record(33333, datetime(2024, 2, 1, tzinfo=UTC), name="NEW")
    older = _record(33333, datetime(2024, 1, 1, tzinfo=UTC), name="OLD")

    upsert_records(conn, [newer])
    result = upsert_records(conn, [older])

    assert result.skipped == 1
    row = conn.execute("SELECT name FROM tle WHERE norad_id = ?", (33333,)).fetchone()
    assert row[0] == "NEW"


def test_upsert_same_epoch_counts_as_skipped() -> None:
    conn = get_connection(":memory:")
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    record = _record(44444, epoch)

    upsert_records(conn, [record])
    result = upsert_records(conn, [record])

    assert result.inserted == 1
    assert result.skipped == 1
