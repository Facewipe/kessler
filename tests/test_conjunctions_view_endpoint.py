"""Tests for GET /conjunctions/{norad_id}/view.

This is a static HTML/CSS/JS shell (same pattern as /demo and /sky) that
fetches GET /conjunctions/{norad_id} from the browser and renders it, so
these tests exercise the shell itself -- shared chrome, controls, the
truncated/empty-state/disclaimer scaffolding -- not the client-rendered
table, which has no server-side equivalent to assert against.
"""

from fastapi.testclient import TestClient

from kessler.api import app

from .conftest import TEST_NORAD_ID

static_client = TestClient(app)


def test_conjunctions_view_returns_html() -> None:
    response = static_client.get("/conjunctions/25544/view")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_conjunctions_view_rejects_non_numeric_norad_id() -> None:
    response = static_client.get("/conjunctions/not-a-number/view")

    assert response.status_code == 422


def test_conjunctions_view_does_not_shadow_json_endpoint(client: TestClient, db_conn) -> None:
    response = client.get(f"/conjunctions/{TEST_NORAD_ID}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "conjunctions" in response.json()


def test_conjunctions_view_includes_shared_nav_and_footer() -> None:
    response = static_client.get("/conjunctions/25544/view")

    assert "kessler-header" in response.text
    assert "Sky view" in response.text
    assert "World map" in response.text
    assert "kessler-footer" in response.text
    assert "Celestrak" in response.text


def test_conjunctions_view_has_window_and_threshold_controls() -> None:
    response = static_client.get("/conjunctions/25544/view")

    assert 'data-hours="24"' in response.text
    assert 'data-hours="72"' in response.text
    assert 'data-hours="168"' in response.text
    assert 'data-threshold="5"' in response.text
    assert 'data-threshold="10"' in response.text
    assert 'data-threshold="25"' in response.text


def test_conjunctions_view_has_truncated_notice_scaffolding() -> None:
    response = static_client.get("/conjunctions/25544/view")

    assert 'id="truncated-notice"' in response.text
    assert "time limit" in response.text.lower()


def test_conjunctions_view_links_accuracy_docs() -> None:
    response = static_client.get("/conjunctions/25544/view")

    assert 'href="/docs/accuracy"' in response.text
    assert "collision probability" in response.text.lower()


def test_conjunctions_view_reads_norad_id_from_url_not_server_template() -> None:
    """The page is one static file for every target -- the JS pulls
    norad_id back out of window.location, it isn't templated server-side --
    so two different targets must get byte-identical shells."""
    first = static_client.get("/conjunctions/25544/view")
    second = static_client.get("/conjunctions/5/view")

    assert first.text == second.text
