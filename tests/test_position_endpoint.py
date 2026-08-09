"""Tests for GET /satellites/{norad_id}/position."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kessler.propagate import epoch_datetime, satrec_from_tle

from .conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

_EPOCH = epoch_datetime(satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2))


def test_position_at_fixed_timestamp(client: TestClient) -> None:
    at = _EPOCH + timedelta(hours=6)

    response = client.get(f"/satellites/{TEST_NORAD_ID}/position", params={"at": at.isoformat()})

    assert response.status_code == 200
    body = response.json()
    assert body["norad_id"] == TEST_NORAD_ID
    assert body["name"] == TEST_SATELLITE_NAME
    assert -90.0 <= body["lat"] <= 90.0
    assert -180.0 <= body["lon"] <= 180.0
    assert body["alt_km"] > 0
    assert body["epoch_age_hours"] == pytest.approx(6.0, abs=0.01)
    assert body["stale"] is False


def test_position_naive_timestamp_is_treated_as_utc(client: TestClient) -> None:
    naive_at = (_EPOCH + timedelta(hours=6)).replace(tzinfo=None).isoformat()

    response = client.get(f"/satellites/{TEST_NORAD_ID}/position", params={"at": naive_at})

    assert response.status_code == 200
    assert response.json()["epoch_age_hours"] == pytest.approx(6.0, abs=0.01)


def test_position_older_than_72h_is_flagged_stale(client: TestClient) -> None:
    at = _EPOCH + timedelta(hours=100)

    response = client.get(f"/satellites/{TEST_NORAD_ID}/position", params={"at": at.isoformat()})

    assert response.status_code == 200
    assert response.json()["stale"] is True


def test_position_defaults_at_to_now(client: TestClient) -> None:
    response = client.get(f"/satellites/{TEST_NORAD_ID}/position")

    assert response.status_code == 200
    body = response.json()
    assert body["at"] is not None
    # The fixture TLE's epoch is year 2000, so "now" is always far past it.
    assert body["stale"] is True


def test_position_unknown_norad_id_is_404(client: TestClient) -> None:
    response = client.get("/satellites/999999/position")

    assert response.status_code == 404


def test_position_invalid_timestamp_is_422(client: TestClient) -> None:
    response = client.get(f"/satellites/{TEST_NORAD_ID}/position", params={"at": "not-a-timestamp"})

    assert response.status_code == 422
