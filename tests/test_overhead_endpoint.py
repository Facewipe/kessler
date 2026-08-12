"""Tests for GET /overhead."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from kessler.db import SatelliteRecord, upsert_satellite
from kessler.propagate import position_at, satrec_from_tle

from .conftest import TEST_NORAD_ID, TEST_TLE_LINE1, TEST_TLE_LINE2
from .test_screen import CLOSE_NORAD_ID, CLOSE_TLE_LINE1, CLOSE_TLE_LINE2


def test_overhead_response_shape_and_defaults(client: TestClient) -> None:
    """Fixed-timestamp numeric assertions on elevation/azimuth live in
    test_overhead.py; this exercises the HTTP layer (defaults, echo, shape),
    since /overhead always propagates to live "now" rather than a fixed `at`."""
    response = client.get("/overhead", params={"lat": 0.0, "lon": 0.0})

    assert response.status_code == 200
    body = response.json()
    assert body["observer"] == {"lat": 0.0, "lon": 0.0, "alt_m": 0.0}
    assert body["min_elevation_deg"] == 10.0
    assert body["count"] == len(body["satellites"])
    for entry in body["satellites"]:
        assert entry.keys() == {
            "norad_id",
            "name",
            "elevation_deg",
            "azimuth_deg",
            "range_km",
            "alt_km",
            "epoch_age_hours",
            "stale",
        }
        assert entry["elevation_deg"] >= 10.0


def test_overhead_sorts_satellites_by_elevation_descending(client: TestClient, db_conn) -> None:
    upsert_satellite(
        db_conn,
        SatelliteRecord(
            norad_id=CLOSE_NORAD_ID, name="CLOSE", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
        ),
    )

    response = client.get("/overhead", params={"lat": 0.0, "lon": 0.0, "min_elevation_deg": 0})

    assert response.status_code == 200
    elevations = [s["elevation_deg"] for s in response.json()["satellites"]]
    assert elevations == sorted(elevations, reverse=True)


def test_overhead_missing_lat_lon_is_422(client: TestClient) -> None:
    response = client.get("/overhead")

    assert response.status_code == 422


@pytest.mark.parametrize("lat", [-91, 91])
def test_overhead_lat_out_of_range_is_422(client: TestClient, lat: float) -> None:
    response = client.get("/overhead", params={"lat": lat, "lon": 0.0})

    assert response.status_code == 422


@pytest.mark.parametrize("lon", [-181, 181])
def test_overhead_lon_out_of_range_is_422(client: TestClient, lon: float) -> None:
    response = client.get("/overhead", params={"lat": 0.0, "lon": lon})

    assert response.status_code == 422


@pytest.mark.parametrize("min_elevation_deg", [-1, 91])
def test_overhead_min_elevation_deg_out_of_range_is_422(
    client: TestClient, min_elevation_deg: float
) -> None:
    response = client.get(
        "/overhead", params={"lat": 0.0, "lon": 0.0, "min_elevation_deg": min_elevation_deg}
    )

    assert response.status_code == 422


def test_overhead_alt_m_is_echoed_back(client: TestClient) -> None:
    response = client.get("/overhead", params={"lat": 51.5074, "lon": -0.1278, "alt_m": 100.0})

    assert response.status_code == 200
    assert response.json()["observer"] == {"lat": 51.5074, "lon": -0.1278, "alt_m": 100.0}


def test_overhead_excludes_satellite_below_min_elevation(client: TestClient) -> None:
    """An observer placed (at request time) antipodal to the seeded
    satellite's current sub-point is guaranteed below its horizon, even at
    min_elevation_deg=0. The few milliseconds between computing "now" here
    and the endpoint's own "now" move the satellite negligibly compared to
    being on the opposite side of the Earth."""
    satrec = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    position = position_at(satrec, datetime.now(UTC))
    antipodal_lat = -position.lat_deg
    antipodal_lon = ((position.lon_deg + 360.0) % 360.0) - 180.0

    response = client.get(
        "/overhead", params={"lat": antipodal_lat, "lon": antipodal_lon, "min_elevation_deg": 0}
    )

    assert response.status_code == 200
    other_ids = {s["norad_id"] for s in response.json()["satellites"]}
    assert TEST_NORAD_ID not in other_ids
