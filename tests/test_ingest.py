"""Tests for kessler.ingest."""

import logging
from pathlib import Path

import pytest

from kessler.db import get_connection
from kessler.ingest import parse_tle_records, run_ingest

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_valid_records() -> None:
    text = (FIXTURES / "tle_valid.txt").read_text()

    records = parse_tle_records(text)

    assert len(records) == 10
    assert records[0].name == "ISS (ZARYA)"
    assert records[0].norad_id == 25544
    assert records[0].line1.startswith("1 25544U")
    assert records[0].line2.startswith("2 25544 ")
    assert records[0].epoch_utc.startswith("2008-")


def test_parse_skips_malformed_records(caplog: pytest.LogCaptureFixture) -> None:
    text = (FIXTURES / "tle_malformed.txt").read_text()

    with caplog.at_level(logging.WARNING):
        records = parse_tle_records(text)

    assert len(records) == 2
    assert {r.norad_id for r in records} == {25544, 20580}
    assert len(caplog.records) == 3


def test_cli_run_twice_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KESSLER_DB", str(tmp_path / "kessler.db"))
    text = (FIXTURES / "tle_valid.txt").read_text()

    first = run_ingest(text)
    second = run_ingest(text)

    assert (first.fetched, first.inserted, first.updated, first.skipped) == (10, 10, 0, 0)
    assert (second.fetched, second.inserted, second.updated, second.skipped) == (10, 0, 0, 10)

    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM tles").fetchone()[0]
    conn.close()
    assert count == 10
