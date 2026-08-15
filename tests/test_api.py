"""Tests for the kessler FastAPI app."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

import kessler.api as kessler_api
from kessler.api import app
from kessler.db import SatelliteRecord, get_connection, upsert_records

client = TestClient(app)


def test_health_on_empty_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KESSLER_DB_PATH", str(tmp_path / "empty.db"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "catalog_size": 0,
        "newest_tle_epoch_utc": None,
        "newest_tle_epoch_age_hours": None,
    }


def test_health_reports_catalog_size_and_newest_epoch_age(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "seeded.db"
    monkeypatch.setenv("KESSLER_DB_PATH", str(db_path))

    conn = get_connection(db_path)
    try:
        upsert_records(
            conn,
            [
                SatelliteRecord(
                    norad_id=1,
                    name="SAT ONE",
                    line1="1 00001U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
                    line2="2 00001  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
                    epoch_utc=datetime.now(UTC),
                )
            ],
        )
    finally:
        conn.close()

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["catalog_size"] == 1
    assert body["newest_tle_epoch_utc"] is not None
    assert body["newest_tle_epoch_age_hours"] >= 0.0


def test_health_does_not_requery_catalog_once_warmed(tmp_path, monkeypatch) -> None:
    """A second /health call must be served from `_health_snapshot`, not by
    querying the catalog again -- the whole point of caching it, so a health
    check never contends with a heavy /overhead or /conjunctions request
    touching the same database."""
    monkeypatch.setenv("KESSLER_DB_PATH", str(tmp_path / "warm.db"))
    call_count = 0
    original_compute = kessler_api._compute_health_snapshot

    def _counting_compute(conn):
        nonlocal call_count
        call_count += 1
        return original_compute(conn)

    monkeypatch.setattr(kessler_api, "_compute_health_snapshot", _counting_compute)

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert call_count == 1


def test_health_snapshot_refreshes_after_ingest(tmp_path) -> None:
    """The cached snapshot must reflect the catalog as of the last ingest,
    not whatever it was when the process (or the cache) first warmed up."""
    db_path = tmp_path / "refresh.db"

    kessler_api._refresh_health_snapshot(str(db_path))
    assert kessler_api._health_snapshot.catalog_size == 0

    conn = get_connection(db_path)
    try:
        upsert_records(
            conn,
            [
                SatelliteRecord(
                    norad_id=1,
                    name="SAT ONE",
                    line1="1 00001U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
                    line2="2 00001  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
                    epoch_utc=datetime.now(UTC),
                )
            ],
        )
    finally:
        conn.close()

    kessler_api._refresh_health_snapshot(str(db_path))
    assert kessler_api._health_snapshot.catalog_size == 1
