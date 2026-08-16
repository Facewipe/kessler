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


def test_sky_conjunctions_link_points_at_the_human_readable_view() -> None:
    """The tooltip's "see conjunctions" link must open the human-readable
    /conjunctions/{id}/view page, not the raw JSON endpoint -- which looks
    broken to anyone who isn't expecting an API response in their browser.
    """
    response = client.get("/sky")

    assert "/conjunctions/' + sat.norad_id + '/view" in response.text


def test_sky_has_two_column_layout_with_overhead_list() -> None:
    """Above 900px the chart should sit beside a scrollable list of what's
    currently overhead (name, NORAD ID, elevation, azimuth, range), not
    leave the rest of a wide viewport empty."""
    response = client.get("/sky")

    assert "@media (min-width: 900px)" in response.text
    assert 'class="sky-layout"' in response.text
    assert 'class="sky-column"' in response.text
    assert 'class="sky-list-column"' in response.text
    assert 'id="sat-list-body"' in response.text
    for header in ("Name", "NORAD", "Elev", "Az", "Range"):
        assert f"<th>{header}</th>" in response.text


def test_sky_list_rows_cross_highlight_with_chart_dots() -> None:
    """Hovering/clicking a list row must highlight (and select) the same
    satellite's chart dot, and vice versa -- both driven off the same
    satIndex entry per norad_id."""
    response = client.get("/sky")

    assert "function rowForMarker(marker)" in response.text
    assert 'row.addEventListener("mouseenter"' in response.text
    assert 'row.addEventListener("click"' in response.text
    assert "satIndex[sat.norad_id] = { sat: sat, group: group, row: row }" in response.text


def test_sky_projection_helpers_are_unchanged() -> None:
    """The polar projection itself must not change as part of the layout
    rework -- only where the chart and list sit on the page."""
    response = client.get("/sky")

    assert "function elevationToRadius(elevationDeg, minElevationDeg) {" in response.text
    assert "function polarPoint(azimuthDeg, elevationDeg, minElevationDeg) {" in response.text
    assert "CHART_R * (90 - clamped) / (90 - minElevationDeg)" in response.text
