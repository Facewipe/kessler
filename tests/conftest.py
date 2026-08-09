"""Shared pytest fixtures for the kessler test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from kessler.api import app, get_db
from kessler.db import SatelliteRecord, get_connection, upsert_satellite

# SGP4 validation test satellite from Vallado, Crawford, Hujsak & Kelso,
# "Revisiting Spacetrack Report #3" (2006) -- a pinned, well-known TLE used
# across SGP4 implementations as a deterministic reference case.
TEST_NORAD_ID = 5
TEST_SATELLITE_NAME = "SGP4-VER TEST SATELLITE 5"
TEST_TLE_LINE1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
TEST_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"


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
