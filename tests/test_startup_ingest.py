"""Tests for startup/periodic catalog auto-ingest (kessler.api)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import kessler.api as kessler_api
from kessler.api import (
    AUTO_INGEST_ENV_VAR,
    _auto_ingest_enabled,
    _ingest_and_log,
    _periodic_ingest_refresh,
    _startup_ingest_if_empty,
)
from kessler.db import SatelliteRecord, count_satellites, get_connection, upsert_satellite

FIXTURES = Path(__file__).parent / "fixtures"


def test_auto_ingest_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(AUTO_INGEST_ENV_VAR, raising=False)

    assert _auto_ingest_enabled() is True


def test_auto_ingest_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv(AUTO_INGEST_ENV_VAR, "0")

    assert _auto_ingest_enabled() is False


def test_startup_ingest_populates_empty_catalog(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))
    text = (FIXTURES / "valid_tles.txt").read_text()
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda: text)

    asyncio.run(_startup_ingest_if_empty())

    conn = get_connection(db_path)
    try:
        assert count_satellites(conn) == 10
    finally:
        conn.close()


def test_startup_ingest_skips_when_catalog_not_empty(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))

    conn = get_connection(db_path)
    try:
        upsert_satellite(
            conn,
            SatelliteRecord(
                norad_id=1,
                name="SAT",
                line1="1 00001U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
                line2="2 00001  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
            ),
        )
    finally:
        conn.close()

    def _boom():
        raise AssertionError("ingest should be skipped when the catalog is not empty")

    monkeypatch.setattr("kessler.ingest.fetch_tle_text", _boom)

    asyncio.run(_startup_ingest_if_empty())  # must not raise


def test_ingest_and_log_logs_summary(monkeypatch, tmp_path, caplog) -> None:
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))
    text = (FIXTURES / "valid_tles.txt").read_text()
    monkeypatch.setattr("kessler.ingest.fetch_tle_text", lambda: text)

    with caplog.at_level("INFO"):
        asyncio.run(_ingest_and_log("test"))

    assert "Catalog ingest (test)" in caplog.text
    assert "inserted: 10" in caplog.text
    assert kessler_api._health_snapshot is not None
    assert kessler_api._health_snapshot.catalog_size == 10


def test_ingest_and_log_swallows_errors(monkeypatch, tmp_path, caplog) -> None:
    db_path = tmp_path / "kessler.db"
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))

    def _boom():
        raise RuntimeError("celestrak is down")

    monkeypatch.setattr("kessler.ingest.fetch_tle_text", _boom)

    with caplog.at_level("ERROR"):
        asyncio.run(_ingest_and_log("test"))  # must not raise

    assert "Catalog ingest (test) failed" in caplog.text
    assert kessler_api._health_snapshot is None


def test_periodic_ingest_refresh_calls_ingest_and_log(monkeypatch) -> None:
    calls = []

    async def fake_ingest_and_log(reason: str) -> None:
        calls.append(reason)

    monkeypatch.setattr("kessler.api._ingest_and_log", fake_ingest_and_log)
    monkeypatch.setattr("kessler.api.INGEST_REFRESH_INTERVAL_HOURS", 0.0)

    async def run() -> None:
        task = asyncio.create_task(_periodic_ingest_refresh())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert calls
    assert all(reason == "scheduled refresh" for reason in calls)
