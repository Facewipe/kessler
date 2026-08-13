"""Tests for GET /sky."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_sky_returns_html() -> None:
    response = client.get("/sky")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()
