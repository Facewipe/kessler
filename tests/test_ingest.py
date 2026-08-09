"""Tests for kessler.ingest."""

from __future__ import annotations

from pathlib import Path

from kessler import db
from kessler.ingest import main, parse_tle_records

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_valid_fixture_returns_all_records():
    text = (FIXTURES / "valid_tles.txt").read_text()

    records = parse_tle_records(text)

    assert len(records) == 10
    assert {r.norad_id for r in records} == {
        25544,
        20580,
        25338,
        28654,
        33591,
        25994,
        27424,
        39084,
        40069,
        5,
    }
    assert records[0].name == "ISS (ZARYA)"
    assert records[0].epoch_utc.year == 2008
    assert records[0].epoch_utc.month == 9
    assert records[0].epoch_utc.day == 20


def test_parse_malformed_fixture_skips_bad_records_and_logs_warning(caplog):
    text = (FIXTURES / "malformed_tles.txt").read_text()

    with caplog.at_level("WARNING"):
        records = parse_tle_records(text)

    assert len(records) == 2
    assert {r.name for r in records} == {"GOOD SAT ONE", "GOOD SAT TWO"}
    assert len(caplog.records) == 3
    assert "bad line prefix" in caplog.text
    assert "NORAD id mismatch" in caplog.text
    assert "bad epoch" in caplog.text


def test_cli_run_twice_does_not_duplicate(tmp_path, monkeypatch):
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB", str(db_path))

    text = (FIXTURES / "valid_tles.txt").read_text()
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda: text)

    main()
    main()

    conn = db.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM tle").fetchone()[0]
    finally:
        conn.close()

    assert count == 10
