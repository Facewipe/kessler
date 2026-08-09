"""Tests for kessler.ingest: TLE parsing and CLI orchestration."""

from pathlib import Path

from kessler.db import get_connection
from kessler.ingest import main, parse_tle_records

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def test_parse_tle_records_valid() -> None:
    text = _load_fixture("celestrak_sample.tle")

    records = parse_tle_records(text)

    assert len(records) == 10
    iss = next(r for r in records if r.norad_id == 25544)
    assert iss.name == "ISS (ZARYA)"
    assert iss.line1.startswith("1 25544")
    assert iss.line2.startswith("2 25544")
    assert iss.epoch_utc.year == 2008
    assert iss.epoch_utc.tzinfo is not None


def test_parse_tle_records_skips_malformed(caplog) -> None:
    text = _load_fixture("celestrak_malformed.tle")

    with caplog.at_level("WARNING"):
        records = parse_tle_records(text)

    assert len(records) == 1
    assert records[0].norad_id == 25544
    assert any("malformed" in message.lower() for message in caplog.messages)


def test_main_cli_run_twice_does_not_create_duplicates(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cli_test.db"
    monkeypatch.setenv("KESSLER_DB", str(db_path))
    text = _load_fixture("celestrak_sample.tle")
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda: text)

    main()
    main()

    conn = get_connection(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM satellites").fetchone()[0]
    finally:
        conn.close()

    assert count == len(parse_tle_records(text))
