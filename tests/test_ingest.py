"""Tests for kessler.ingest."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from kessler import db
from kessler.ingest import (
    CELESTRAK_URL,
    USER_AGENT,
    fetch_tle_text,
    main,
    parse_tle_records,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _response(status_code: int, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", CELESTRAK_URL)
    return httpx.Response(status_code, request=request, text=text)


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
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))

    text = (FIXTURES / "valid_tles.txt").read_text()
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda **kwargs: text)

    main()
    main()

    conn = db.get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM satellites").fetchone()[0]
    finally:
        conn.close()

    assert count == 10


def test_fetch_sends_descriptive_user_agent(monkeypatch):
    captured_headers = {}

    def fake_get(url, params, timeout, headers):
        captured_headers.update(headers)
        return _response(200, text="catalog")

    monkeypatch.setattr("kessler.ingest.httpx.get", fake_get)

    text = fetch_tle_text(cache_path=None)

    assert text == "catalog"
    assert captured_headers["User-Agent"] == USER_AGENT
    assert "kessler" in USER_AGENT.lower()


def test_fetch_retries_with_backoff_on_403_then_succeeds(monkeypatch):
    responses = [_response(403), _response(429), _response(200, text="catalog")]
    sleeps = []

    monkeypatch.setattr("kessler.ingest.httpx.get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr("kessler.ingest.time.sleep", lambda seconds: sleeps.append(seconds))

    text = fetch_tle_text(cache_path=None, max_retries=3)

    assert text == "catalog"
    assert sleeps == [1.0, 2.0]  # exponential backoff, one sleep per retried attempt


def test_fetch_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("kessler.ingest.httpx.get", lambda *a, **k: _response(403))
    monkeypatch.setattr("kessler.ingest.time.sleep", lambda seconds: None)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_tle_text(cache_path=None, max_retries=2)


def test_fetch_does_not_retry_non_rate_limit_errors(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return _response(500)

    monkeypatch.setattr("kessler.ingest.httpx.get", fake_get)
    monkeypatch.setattr("kessler.ingest.time.sleep", lambda seconds: None)

    with pytest.raises(httpx.HTTPStatusError):
        fetch_tle_text(cache_path=None, max_retries=3)

    assert len(calls) == 1


def test_fetch_reuses_fresh_cache_without_network_call(tmp_path, monkeypatch):
    cache_path = tmp_path / "gp.tle"
    cache_path.write_text("cached catalog")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("network should not be called when cache is fresh")

    monkeypatch.setattr("kessler.ingest.httpx.get", fail_if_called)

    text = fetch_tle_text(cache_path=cache_path, cache_ttl_seconds=3600)

    assert text == "cached catalog"


def test_fetch_refetches_when_cache_is_stale(tmp_path, monkeypatch):
    cache_path = tmp_path / "gp.tle"
    cache_path.write_text("stale catalog")
    old_time = cache_path.stat().st_mtime - 3600
    os.utime(cache_path, (old_time, old_time))

    monkeypatch.setattr(
        "kessler.ingest.httpx.get", lambda *a, **k: _response(200, text="fresh catalog")
    )

    text = fetch_tle_text(cache_path=cache_path, cache_ttl_seconds=60)

    assert text == "fresh catalog"
    assert cache_path.read_text() == "fresh catalog"


def test_fetch_writes_cache_after_network_fetch(tmp_path, monkeypatch):
    cache_path = tmp_path / "nested" / "gp.tle"

    monkeypatch.setattr("kessler.ingest.httpx.get", lambda *a, **k: _response(200, text="catalog"))

    fetch_tle_text(cache_path=cache_path)

    assert cache_path.read_text() == "catalog"
