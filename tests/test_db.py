"""Tests for kessler.db."""

import pytest

from kessler.db import DEFAULT_DB_PATH, TLERecord, get_connection, get_db_path, upsert_records


def _record(norad_id: int, epoch_utc: str, name: str = "SAT") -> TLERecord:
    return TLERecord(
        name=name,
        norad_id=norad_id,
        line1="1 00001U 00000A   24001.00000000  .00000000  00000-0  00000-0 0  0000",
        line2="2 00001  00.0000 000.0000 0000000 000.0000 000.0000 15.00000000000000",
        epoch_utc=epoch_utc,
    )


def test_insert_new_record() -> None:
    conn = get_connection(":memory:")

    result = upsert_records(
        conn, [_record(1, "2024-01-01T00:00:00+00:00")], "2024-01-02T00:00:00+00:00"
    )

    assert (result.inserted, result.updated, result.skipped) == (1, 0, 0)
    row = conn.execute("SELECT norad_id, epoch_utc FROM tles").fetchone()
    assert row == (1, "2024-01-01T00:00:00+00:00")


def test_upsert_keeps_newest_epoch() -> None:
    conn = get_connection(":memory:")
    upsert_records(conn, [_record(1, "2024-01-01T00:00:00+00:00")], "2024-01-02T00:00:00+00:00")

    result = upsert_records(
        conn, [_record(1, "2024-06-01T00:00:00+00:00")], "2024-06-02T00:00:00+00:00"
    )

    assert (result.inserted, result.updated, result.skipped) == (0, 1, 0)
    epoch = conn.execute("SELECT epoch_utc FROM tles WHERE norad_id = 1").fetchone()[0]
    assert epoch == "2024-06-01T00:00:00+00:00"


def test_upsert_does_not_regress_to_older_epoch() -> None:
    conn = get_connection(":memory:")
    upsert_records(conn, [_record(1, "2024-06-01T00:00:00+00:00")], "2024-06-02T00:00:00+00:00")

    result = upsert_records(
        conn, [_record(1, "2024-01-01T00:00:00+00:00")], "2024-01-02T00:00:00+00:00"
    )

    assert (result.inserted, result.updated, result.skipped) == (0, 0, 1)
    epoch = conn.execute("SELECT epoch_utc FROM tles WHERE norad_id = 1").fetchone()[0]
    assert epoch == "2024-06-01T00:00:00+00:00"


def test_get_db_path_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KESSLER_DB", "/tmp/custom.db")

    assert get_db_path() == "/tmp/custom.db"


def test_get_db_path_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KESSLER_DB", raising=False)

    assert get_db_path() == DEFAULT_DB_PATH
