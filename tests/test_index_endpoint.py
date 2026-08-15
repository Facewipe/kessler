"""Tests for GET / and the shared static assets it links to."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_index_returns_html() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_index_includes_shared_nav() -> None:
    response = client.get("/")

    assert "kessler-header" in response.text
    assert "Sky view" in response.text
    assert "World map" in response.text
    assert ">API<" in response.text


def test_index_includes_footer() -> None:
    response = client.get("/")

    assert "kessler-footer" in response.text
    assert "Celestrak" in response.text
    assert "/docs/accuracy" in response.text


def test_shared_css_is_served() -> None:
    response = client.get("/static/shared.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_accuracy_docs_page_returns_html() -> None:
    response = client.get("/docs/accuracy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "epoch_age_hours" in response.text
    assert "kessler-footer" in response.text
