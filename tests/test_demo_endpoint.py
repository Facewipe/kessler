"""Tests for GET /demo and GET /world.json."""

import re

from fastapi.testclient import TestClient

from kessler.api import app

client = TestClient(app)


def test_demo_returns_html() -> None:
    response = client.get("/demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_demo_includes_shared_nav_and_footer() -> None:
    response = client.get("/demo")

    assert "kessler-header" in response.text
    assert "Sky view" in response.text
    assert 'href="/demo" class="active"' in response.text
    assert "kessler-footer" in response.text
    assert "Celestrak" in response.text


def test_demo_status_and_legend_are_stacked_not_fixed_overlays() -> None:
    """Regression test: the status line and footer used to both be
    `position: fixed` at a hardcoded `bottom` offset and would overlap on
    narrow screens where the footer wraps to multiple lines. They must now
    flow in normal document order inside a shared, non-fixed wrapper."""
    response = client.get("/demo")

    assert 'id="info-bar"' in response.text
    info_bar_rule = re.search(r"#info-bar\s*\{[^}]*\}", response.text)
    assert info_bar_rule is not None
    assert "position: fixed" not in info_bar_rule.group()
    assert "position:fixed" not in info_bar_rule.group()


def test_demo_plots_a_live_catalog_sample_colored_by_regime() -> None:
    """Regression test: the map used to plot a hardcoded list of ~20
    curated satellites, one HTTP request each. It must now fetch a live,
    server-sampled batch from the bulk positions endpoint and colour
    markers by orbit regime (matching the sky view's legend), not by
    fresh/stale TLE status."""
    response = client.get("/demo")

    assert "/satellites/positions" in response.text
    assert "SATELLITES = [" not in response.text
    assert "REGIME_COLOR" in response.text
    assert "LEO (&lt;2000 km)" in response.text
    assert "MEO (2000&ndash;35000 km)" in response.text
    assert "GEO (&gt;35000 km)" in response.text


def test_world_json_returns_land_and_lake_polygons() -> None:
    response = client.get("/world.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    data = response.json()
    assert isinstance(data, dict)
    assert set(data.keys()) == {"land", "lakes"}

    for key in ("land", "lakes"):
        polygons = data[key]
        assert isinstance(polygons, list)
        assert len(polygons) > 0
        for polygon in polygons:
            assert len(polygon) >= 3
            for point in polygon:
                lon, lat = point
                assert -180 <= lon <= 180
                assert -90 <= lat <= 90


def test_demo_renders_lakes_on_top_of_land_in_the_ocean_colour() -> None:
    """Regression test: ne_110m_land has no cutouts for large inland water
    bodies (the Great Lakes, Lake Baikal, ...), so they used to render as
    land. Lakes must now be drawn in the ocean colour on top of the land
    layer, not subtracted from it (no geometry library available)."""
    response = client.get("/demo")

    assert 'id="lake-layer"' in response.text
    assert "data.lakes" in response.text
    lake_rule = re.search(r"\.lake\s*\{[^}]*\}", response.text)
    assert lake_rule is not None
    assert "var(--ocean)" in lake_rule.group()


def test_demo_widens_land_ocean_contrast() -> None:
    """Regression test: --land and --ocean used to be nearly the same
    tone (relative-luminance contrast ~1.1:1), so landmasses barely read
    against the sea. Pins the widened palette."""
    response = client.get("/demo")

    assert "--ocean: #0a0f1a;" in response.text
    assert "--land: #3a5c8c;" in response.text
