"""Tests for GET /sky."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_sky_returns_html() -> None:
    response = client.get("/sky")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_sky_includes_shared_nav_and_footer() -> None:
    response = client.get("/sky")

    assert "kessler-header" in response.text
    assert "World map" in response.text
    assert 'href="/sky" class="active"' in response.text
    assert "kessler-footer" in response.text
    assert "Celestrak" in response.text
