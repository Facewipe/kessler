"""Tests for kessler.ingest: TLE parsing, fetching, and the CLI."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kessler import ingest
from kessler.db import get_connection

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_valid_records_returns_all_ten() -> None:
    records = ingest.parse_tle_records(_read_fixture("celestrak_sample.tle"))

    assert len(records) == 10
    assert {record.norad_id for record in records} >= {25544, 20580}

    iss = next(record for record in records if record.norad_id == 25544)
    assert iss.name == "ISS (ZARYA)"

    hst = next(record for record in records if record.norad_id == 20580)
    assert hst.name == "HST"
    assert hst.epoch_utc == datetime(2024, 2, 14, 12, 0, 0, tzinfo=UTC)


def test_parse_malformed_records_are_skipped_with_warning(caplog) -> None:
    text = _read_fixture("celestrak_malformed.tle")

    with caplog.at_level("WARNING"):
        records = ingest.parse_tle_records(text)

    assert len(records) == 1
    assert records[0].norad_id == 20580
    assert len(caplog.records) == 3


def test_parse_epoch_before_year_2000_uses_19xx() -> None:
    line1 = "1 00005U 98067A   97365.50000000 -.00002182  00000-0 -11606-4 0  2927"
    line2 = "2 00005  34.2497 348.7242 1846459 265.7215 073.6675 10.84381648034743"
    text = f"VANGUARD 1\n{line1}\n{line2}\n"

    records = ingest.parse_tle_records(text)

    assert len(records) == 1
    assert records[0].epoch_utc.year == 1997


def test_cli_run_twice_does_not_duplicate(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB", str(db_path))
    sample_text = _read_fixture("celestrak_sample.tle")

    with patch.object(ingest, "fetch_tle_text", return_value=sample_text):
        ingest.main()
        ingest.main()

    conn = get_connection(str(db_path))
    row_count = conn.execute("SELECT COUNT(*) FROM tle").fetchone()[0]
    assert row_count == 10
