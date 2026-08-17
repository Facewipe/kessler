"""Regression test for the world map's mobile letterboxing bug.

kessler/static/demo.html's #map-container fills the available viewport
height (flex: 1). On a portrait phone that makes it much taller than it
is wide, but the map's SVG had a fixed `viewBox="0 0 360 180"` (a 2:1
rect) with `preserveAspectRatio="xMidYMid meet"`, which fits by shrinking
to the container's *width* -- leaving a huge letterboxed strip of plain
ocean above and below the actual map ("squashed into a narrow strip...
with a large empty area below it").

demo.html's `fitMapViewBox()` fixes this by growing the viewBox on
whichever axis the container is more generous on, centered on the
equator/prime meridian, so the rendered map always exactly fills the
container at a uniform scale with no letterboxing and no distortion.

There's no JS test runner in this project, so this mirrors that function
in Python (matching tests/test_world_map_projection.py's approach for the
same file) and checks its output has no letterbox for representative
container sizes, including the reported 380px-wide phone case.
"""

from __future__ import annotations

BASE_WIDTH = 360.0
BASE_HEIGHT = 180.0


def _fit_view_box(
    container_width: float, container_height: float
) -> tuple[float, float, float, float]:
    """Mirrors demo.html's `fitMapViewBox()`. Returns (min_x, min_y, width, height)."""
    container_aspect = container_width / container_height
    base_aspect = BASE_WIDTH / BASE_HEIGHT

    width, height = BASE_WIDTH, BASE_HEIGHT
    if container_aspect > base_aspect:
        width = BASE_HEIGHT * container_aspect
    else:
        height = BASE_WIDTH / container_aspect

    min_x = (BASE_WIDTH - width) / 2
    min_y = (BASE_HEIGHT - height) / 2
    return min_x, min_y, width, height


def _aspect_matches(container_width: float, container_height: float) -> bool:
    _min_x, _min_y, width, height = _fit_view_box(container_width, container_height)
    return abs((width / height) - (container_width / container_height)) < 1e-9


def test_portrait_phone_at_380px_width_has_no_letterbox() -> None:
    """The reported case: a narrow, tall container (e.g. a 380px-wide phone
    with the map filling most of the viewport height) must get a viewBox
    whose aspect ratio matches the container exactly, not the fixed 2:1
    of the underlying (lon, lat) data."""
    assert _aspect_matches(380, 640)
    assert _aspect_matches(380, 900)


def test_portrait_container_grows_height_not_width() -> None:
    """A taller-than-2:1 container should extend the viewBox's latitude
    span (more ocean above/below), keeping the full 360-wide longitude
    range visible rather than cropping it."""
    min_x, min_y, width, height = _fit_view_box(380, 700)

    assert width == BASE_WIDTH
    assert height > BASE_HEIGHT
    assert min_x == 0
    assert min_y < 0


def test_wide_container_grows_width_not_height() -> None:
    """A wider-than-2:1 container (e.g. an ultrawide desktop window) should
    extend the viewBox's longitude span instead, keeping the full -90..90
    latitude range visible."""
    min_x, min_y, width, height = _fit_view_box(2000, 500)

    assert height == BASE_HEIGHT
    assert width > BASE_WIDTH
    assert min_y == 0
    assert min_x < 0


def test_container_matching_native_2_to_1_aspect_is_unchanged() -> None:
    assert _fit_view_box(720, 360) == (0, 0, BASE_WIDTH, BASE_HEIGHT)


def test_demo_wires_up_fit_map_view_box() -> None:
    """Guards against the JS drifting out of sync with the Python mirror
    above -- if fitMapViewBox is renamed or stops being called, update
    both this check and the mirror function's docstring."""
    from pathlib import Path

    html = (
        Path(__file__)
        .parent.parent.joinpath("kessler", "static", "demo.html")
        .read_text(encoding="utf-8")
    )

    assert "function fitMapViewBox()" in html
    assert "fitMapViewBox();" in html
    assert 'window.addEventListener("resize", fitMapViewBox)' in html
