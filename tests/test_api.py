"""Tests for the kessler FastAPI app."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

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
