"""Shared pytest fixtures for the kessler test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import kessler.api as kessler_api
from kessler.api import AUTO_INGEST_ENV_VAR, app, get_db
from kessler.api import _overhead_cache as api_overhead_cache
from kessler.api import _screening_cache as api_screening_cache
from kessler.catalog_cache import reset_cache as reset_catalog_cache
from kessler.db import SatelliteRecord, get_connection, upsert_satellite

# SGP4 validation test satellite from Vallado, Crawford, Hujsak & Kelso,
# "Revisiting Spacetrack Report #3" (2006) -- a pinned, well-known TLE used
# across SGP4 implementations as a deterministic reference case.
TEST_NORAD_ID = 5
TEST_SATELLITE_NAME = "SGP4-VER TEST SATELLITE 5"
TEST_TLE_LINE1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
TEST_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"


@pytest.fixture(autouse=True)
def _disable_auto_ingest(monkeypatch):
    """Disable startup/periodic auto-ingest for every test, so a test that
    exercises the app's lifespan (via `with TestClient(app) as ...`) never
    makes a real network call to Celestrak.
    """
    monkeypatch.setenv(AUTO_INGEST_ENV_VAR, "0")


@pytest.fixture(autouse=True)
def _reset_screening_caches(monkeypatch):
    """Clear every process-wide cache (screening results, overhead results,
    the parsed-catalog cache, and the /health snapshot) before and after
    every test.

    All are keyed independently of which SQLite file backs them (e.g. the
    screening cache by (norad_id, hours, threshold_km, min_separation_km);
    the catalog cache by a (norad_id, epoch) signature; the health snapshot
    isn't keyed at all), while every test gets its own temporary DB via
    `db_conn`. Without this, a cache entry -- or the health snapshot --
    written by one test could be served, stale, to a different test that
    happens to reuse the same norad_id/params/no-params against different
    seeded data.
    """
    api_screening_cache.clear()
    api_overhead_cache.clear()
    reset_catalog_cache()
    monkeypatch.setattr(kessler_api, "_health_snapshot", None)
    yield
    api_screening_cache.clear()
    api_overhead_cache.clear()
    reset_catalog_cache()


@pytest.fixture
def db_conn(tmp_path):
    """A temporary SQLite catalog seeded with the pinned test satellite."""
    conn = get_connection(tmp_path / "test.db")
    upsert_satellite(
        conn,
        SatelliteRecord(
            norad_id=TEST_NORAD_ID,
            name=TEST_SATELLITE_NAME,
            line1=TEST_TLE_LINE1,
            line2=TEST_TLE_LINE2,
        ),
    )
    yield conn
    conn.close()


@pytest.fixture
def client(db_conn) -> Iterator[TestClient]:
    """A TestClient wired to the seeded temporary catalog."""
    app.dependency_overrides[get_db] = lambda: db_conn
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
