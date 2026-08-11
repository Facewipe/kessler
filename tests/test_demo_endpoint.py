"""Tests for GET /demo."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_demo_returns_html() -> None:
    response = client.get("/demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()
