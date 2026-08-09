"""Tests for kessler.db."""

from datetime import datetime, timezone
from pathlib import Path

from kessler.db import DEFAULT_DB_PATH, TLERecord, get_connection, get_db_path, upsert_records


def _record(norad_id: int, epoch: datetime, name: str = "TEST SAT") -> TLERecord:
    return TLERecord(
        name=name,
        norad_id=norad_id,
        line1=f"1 {norad_id:05d}U 24001A   24001.50000000  .00000000  00000-0  00000-0 0 00010",
        line2=f"2 {norad_id:05d} 098.2000 150.0000 0001500 090.0000 270.0000 14.00000000000010",
        epoch_utc=epoch,
    )


def test_get_db_path_default(monkeypatch) -> None:
    monkeypatch.delenv("KESSLER_DB", raising=False)

    assert get_db_path() == Path(DEFAULT_DB_PATH)


def test_get_db_path_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KESSLER_DB", str(tmp_path / "custom.db"))

    assert get_db_path() == tmp_path / "custom.db"


def test_upsert_inserts_new_record(tmp_path) -> None:
    conn = get_connection(tmp_path / "test.db")

    result = upsert_records(conn, [_record(25544, datetime(2024, 1, 1, tzinfo=timezone.utc))])

    assert (result.inserted, result.updated, result.skipped) == (1, 0, 0)
    row = conn.execute("SELECT norad_id FROM tles").fetchone()
    assert row[0] == 25544


def test_upsert_keeps_newest_epoch(tmp_path) -> None:
    conn = get_connection(tmp_path / "test.db")
    older = _record(25544, datetime(2024, 1, 1, tzinfo=timezone.utc))
    newer = _record(25544, datetime(2024, 1, 5, tzinfo=timezone.utc))

    upsert_records(conn, [older])
    result = upsert_records(conn, [newer])

    assert (result.inserted, result.updated, result.skipped) == (0, 1, 0)
    row = conn.execute("SELECT epoch_utc FROM tles WHERE norad_id = 25544").fetchone()
    assert row[0] == newer.epoch_utc.isoformat()


def test_upsert_does_not_regress_to_older_epoch(tmp_path) -> None:
    conn = get_connection(tmp_path / "test.db")
    newer = _record(25544, datetime(2024, 1, 5, tzinfo=timezone.utc))
    older = _record(25544, datetime(2024, 1, 1, tzinfo=timezone.utc))

    upsert_records(conn, [newer])
    result = upsert_records(conn, [older])

    assert (result.inserted, result.updated, result.skipped) == (0, 0, 1)
    row = conn.execute("SELECT epoch_utc FROM tles WHERE norad_id = 25544").fetchone()
    assert row[0] == newer.epoch_utc.isoformat()
