"""Tests for GET /conjunctions/{norad_id}."""

import pytest
from fastapi.testclient import TestClient

import kessler.api as kessler_api
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
    assert body["truncated"] is False
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


def test_conjunctions_repeated_request_is_served_from_cache(
    client: TestClient, db_conn, monkeypatch
) -> None:
    """A second request with identical (norad_id, hours, threshold_km,
    min_separation_km) must be served from cache, not re-screened -- the
    whole point of caching this expensive endpoint."""
    _seed_catalog(db_conn)
    call_count = 0
    original_screen_catalog = kessler_api.screen_catalog

    def _counting_screen_catalog(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_screen_catalog(*args, **kwargs)

    monkeypatch.setattr(kessler_api, "screen_catalog", _counting_screen_catalog)

    params = {"hours": 1, "threshold_km": 50}
    first = client.get(f"/conjunctions/{TEST_NORAD_ID}", params=params)
    second = client.get(f"/conjunctions/{TEST_NORAD_ID}", params=params)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert call_count == 1


def test_conjunctions_different_params_are_not_conflated_by_cache(
    client: TestClient, db_conn
) -> None:
    """Different min_separation_km values must not share a cache entry, even
    though it isn't part of the endpoint's primary (norad_id, hours,
    threshold_km) cache key -- serving one request's result for another's
    parameters would silently change what's reported."""
    _seed_catalog(db_conn)

    narrow = client.get(
        f"/conjunctions/{TEST_NORAD_ID}",
        params={"hours": 1, "threshold_km": 50, "min_separation_km": 0},
    )
    wide = client.get(
        f"/conjunctions/{TEST_NORAD_ID}",
        params={"hours": 1, "threshold_km": 50, "min_separation_km": 50},
    )

    assert narrow.status_code == 200
    assert wide.status_code == 200
    assert narrow.json()["min_separation_km"] != wide.json()["min_separation_km"]


def test_conjunctions_truncated_when_time_budget_exhausted(
    client: TestClient, db_conn, monkeypatch
) -> None:
    """An exhausted time budget must surface as `truncated: true`, not a
    hang -- this is the actual production fix, exercised end-to-end through
    the API rather than just at the `screen_catalog` unit level."""
    _seed_catalog(db_conn)
    monkeypatch.setattr(kessler_api, "SCREENING_TIME_BUDGET_SECONDS", 0.0)

    response = client.get(f"/conjunctions/{TEST_NORAD_ID}", params={"threshold_km": 50})

    assert response.status_code == 200
    assert response.json()["truncated"] is True
