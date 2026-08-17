"""Regression test: /docs (Swagger UI) used to be a dead end with no way
back to the rest of the site. FastAPI renders `info.description` as
Markdown in Swagger UI, so navigation links belong there."""

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_openapi_description_links_back_to_the_site() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    description = response.json()["info"]["description"]

    assert "(/)" in description
    assert "(/sky)" in description
    assert "(/demo)" in description


def test_docs_ui_is_served() -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
