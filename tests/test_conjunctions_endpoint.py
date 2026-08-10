"""Tests for GET /conjunctions/{norad_id}."""

import pytest
from fastapi.testclient import TestClient

from kessler.db import SatelliteRecord, upsert_satellite

from .conftest import TEST_NORAD_ID, TEST_TLE_LINE1, TEST_TLE_LINE2
from .test_screen import (
    CLOSE_NORAD_ID,
    CLOSE_TLE_LINE1,
    CLOSE_TLE_LINE2,
    DOCKED_NORAD_ID,
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
    assert body["min_separation_km"] == 1.0
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


@pytest.mark.parametrize("min_separation_km", [-1, 51])
def test_conjunctions_min_separation_km_out_of_range_is_422(
    client: TestClient, min_separation_km: int
) -> None:
    response = client.get(
        f"/conjunctions/{TEST_NORAD_ID}", params={"min_separation_km": min_separation_km}
    )

    assert response.status_code == 422


def test_conjunctions_excludes_docked_object(client: TestClient, db_conn) -> None:
    """A catalog entry sharing the target's exact orbit (a docked module) is
    excluded as co-located, not reported as a zero-distance conjunction."""
    upsert_satellite(
        db_conn,
        SatelliteRecord(
            norad_id=DOCKED_NORAD_ID,
            name="DOCKED-MODULE",
            line1=TEST_TLE_LINE1,
            line2=TEST_TLE_LINE2,
        ),
    )

    response = client.get(f"/conjunctions/{TEST_NORAD_ID}", params={"threshold_km": 50})

    assert response.status_code == 200
    other_ids = {entry["other_norad_id"] for entry in response.json()["conjunctions"]}
    assert DOCKED_NORAD_ID not in other_ids
