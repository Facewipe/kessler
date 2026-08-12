"""Tests for GET /demo and GET /world.json."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_demo_returns_html() -> None:
    response = client.get("/demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_world_json_returns_land_polygons() -> None:
    response = client.get("/world.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    polygons = response.json()
    assert isinstance(polygons, list)
    assert len(polygons) > 0
    for polygon in polygons:
        assert len(polygon) >= 3
        for point in polygon:
            lon, lat = point
            assert -180 <= lon <= 180
            assert -90 <= lat <= 90
