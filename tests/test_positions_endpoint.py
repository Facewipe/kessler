"""Tests for GET /satellites/positions."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import kessler.api as kessler_api
from kessler.db import SatelliteRecord, upsert_records

from .conftest import TEST_NORAD_ID
from .test_screen import CLOSE_TLE_LINE1, CLOSE_TLE_LINE2


def _seed_many(db_conn, count: int, start_norad_id: int = 100000) -> None:
    now = datetime.now(UTC)
    records = [
        SatelliteRecord(
            norad_id=start_norad_id + i,
            name=f"BULK-{i}",
            line1=CLOSE_TLE_LINE1,
            line2=CLOSE_TLE_LINE2,
            epoch_utc=now - timedelta(hours=1),
        )
        for i in range(count)
    ]
    upsert_records(db_conn, records)


def test_positions_response_shape(client: TestClient) -> None:
    response = client.get("/satellites/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["satellites"])
    assert body["count"] == 1  # only the fixture-seeded TEST_NORAD_ID is present
    for entry in body["satellites"]:
        assert entry.keys() == {
            "norad_id",
            "name",
            "lat",
            "lon",
            "alt_km",
            "epoch_age_hours",
            "stale",
        }


def test_positions_respects_limit(client: TestClient, db_conn) -> None:
    _seed_many(db_conn, 50)

    response = client.get("/satellites/positions", params={"limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] <= 10
    assert len(body["satellites"]) <= 10


def test_positions_returns_up_to_the_full_catalog_when_limit_is_large(
    client: TestClient, db_conn
) -> None:
    _seed_many(db_conn, 50)

    response = client.get("/satellites/positions", params={"limit": 1000})

    assert response.status_code == 200
    # 50 seeded + the 1 fixture-seeded TEST_NORAD_ID satellite.
    assert response.json()["count"] == 51


def test_positions_sample_spans_the_catalog_not_just_the_first_records(
    client: TestClient, db_conn
) -> None:
    """A small `limit` against a much larger catalog must not always return
    the same low-norad_id records -- the stride sampling should reach norad
    ids spread across the whole catalog."""
    _seed_many(db_conn, 200, start_norad_id=100000)

    response = client.get("/satellites/positions", params={"limit": 20})

    assert response.status_code == 200
    norad_ids = [s["norad_id"] for s in response.json()["satellites"]]
    assert max(norad_ids) > 100000 + 100  # reaches into the back half of the catalog


@pytest.mark.parametrize("limit", [0, 1001])
def test_positions_limit_out_of_range_is_422(client: TestClient, limit: int) -> None:
    response = client.get("/satellites/positions", params={"limit": limit})

    assert response.status_code == 422


def test_positions_uses_catalog_cache_without_reparsing(
    client: TestClient, db_conn, monkeypatch
) -> None:
    """Once the catalog cache is warm (e.g. from an /overhead or
    /conjunctions call), /satellites/positions must reuse it instead of
    re-parsing every TLE -- the whole point of sharing the cache."""
    client.get("/overhead", params={"lat": 0.0, "lon": 0.0})  # warms the catalog cache

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("satrec_from_tle must not be called when the catalog cache is warm")

    monkeypatch.setattr(kessler_api, "satrec_from_tle", _fail_if_called)

    response = client.get("/satellites/positions")

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_positions_includes_test_satellite(client: TestClient) -> None:
    response = client.get("/satellites/positions", params={"limit": 5})

    assert response.status_code == 200
    norad_ids = {s["norad_id"] for s in response.json()["satellites"]}
    assert TEST_NORAD_ID in norad_ids
