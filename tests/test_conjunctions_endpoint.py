"""Tests for GET /conjunctions/{norad_id}."""

import pytest
from fastapi.testclient import TestClient

from kessler.db import SatelliteRecord, upsert_satellite

from .conftest import TEST_NORAD_ID
from .test_screen import (
    CLOSE_NORAD_ID,
    CLOSE_TLE_LINE1,
    CLOSE_TLE_LINE2,
    FAR_NORAD_ID,
    FAR_TLE_LINE1,
    FAR_TLE_LINE2,
)


def _seed_catalog(db_conn) -> None:
    upsert_satellite(
        db_conn,
        SatelliteRecord(
            norad_id=CLOSE_NORAD_ID, name="CLOSE", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
        ),
    )
    upsert_satellite(
        db_conn,
        SatelliteRecord(
            norad_id=FAR_NORAD_ID, name="FAR", line1=FAR_TLE_LINE1, line2=FAR_TLE_LINE2
        ),
    )


def test_conjunctions_includes_disclaimer_and_window(client: TestClient, db_conn) -> None:
    _seed_catalog(db_conn)

    response = client.get(f"/conjunctions/{TEST_NORAD_ID}", params={"hours": 1, "threshold_km": 50})

    assert response.status_code == 200
    body = response.json()
    assert "collision probability" in body["disclaimer"]
    assert body["target_norad_id"] == TEST_NORAD_ID
    assert body["threshold_km"] == 50
    assert isinstance(body["conjunctions"], list)
    for entry in body["conjunctions"]:
        assert entry.keys() == {
            "other_norad_id",
            "other_name",
            "tca_utc",
            "miss_distance_km",
            "target_epoch_age_hours",
            "other_epoch_age_hours",
        }


def test_conjunctions_unknown_norad_id_is_404(client: TestClient) -> None:
    response = client.get("/conjunctions/999999")

    assert response.status_code == 404


@pytest.mark.parametrize("hours", [0, 169])
def test_conjunctions_hours_out_of_range_is_422(client: TestClient, hours: int) -> None:
    response = client.get(f"/conjunctions/{TEST_NORAD_ID}", params={"hours": hours})

    assert response.status_code == 422


@pytest.mark.parametrize("threshold_km", [0, 51])
def test_conjunctions_threshold_km_out_of_range_is_422(
    client: TestClient, threshold_km: int
) -> None:
    response = client.get(f"/conjunctions/{TEST_NORAD_ID}", params={"threshold_km": threshold_km})

    assert response.status_code == 422
