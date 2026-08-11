"""Tests for the kessler FastAPI app."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_demo_page_serves_html_with_utf8_charset() -> None:
    response = client.get("/demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert '<meta charset="utf-8" />' in response.text
    assert "kessler — live demo" in response.text
