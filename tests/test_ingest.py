"""Tests for kessler.ingest."""

from datetime import datetime, timezone
from pathlib import Path

from kessler.db import get_connection
from kessler.ingest import main, parse_tle_records

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_valid_records() -> None:
    text = (FIXTURES_DIR / "celestrak_sample.tle").read_text()

    records = parse_tle_records(text)

    assert len(records) == 10
    assert records[0].name == "ISS (ZARYA)"
    assert records[0].norad_id == 25544
    assert records[0].epoch_utc == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert records[-1].name == "VANGUARD 2"
    assert records[-1].norad_id == 5


def test_parse_skips_malformed_records(caplog) -> None:
    text = (FIXTURES_DIR / "celestrak_malformed.tle").read_text()

    with caplog.at_level("WARNING"):
        records = parse_tle_records(text)

    assert [r.name for r in records] == ["ISS (ZARYA)", "HST"]
    assert "bad line prefix" in caplog.text
    assert "NORAD ID mismatch" in caplog.text
    assert "unparsable epoch" in caplog.text


def test_cli_run_twice_does_not_duplicate(tmp_path, monkeypatch) -> None:
    fixture_text = (FIXTURES_DIR / "celestrak_sample.tle").read_text()
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda: fixture_text)
    monkeypatch.setenv("KESSLER_DB", str(tmp_path / "cli_test.db"))

    main()
    main()

    conn = get_connection(tmp_path / "cli_test.db")
    count = conn.execute("SELECT COUNT(*) FROM tles").fetchone()[0]
    assert count == 10
