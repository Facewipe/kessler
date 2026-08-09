"""Tests for the kessler FastAPI app."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
