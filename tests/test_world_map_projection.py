"""Regression test for the world map's antimeridian rendering bug.

kessler/static/demo.html projects (lon, lat) points from world.json onto a
0-360 x 0-180 SVG canvas with a small `project()` function. The mapping
`normLon = ((lon + 180) % 360 + 360) % 360 - 180` puts the antimeridian at
-180 regardless of whether the input was +180 or -180, so a coastline point
sitting exactly on +180 (e.g. Antarctica's and Eurasia's rings, which touch
it exactly after world.json's coordinate rounding -- see
scripts/build_map.py) collapsed onto -180's x=0 instead of staying at the
right edge (x=360). That single-point fold drew a straight line all the way
across the map at that point's latitude -- the reported "horizontal
banding" -- for every ring that touches the antimeridian.

There's no JS test runner in this project, so this mirrors demo.html's
fixed `project()` in Python and pins its exact boundary behavior, then
checks it against every point in the real, shipped world.json. If
demo.html's `project()` changes, update `_project()` below to match.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WORLD_JSON_PATH = Path(__file__).parent.parent / "kessler" / "static" / "world.json"


def _project(lon: float, lat: float) -> tuple[float, float]:
    """Mirrors demo.html's `project(lon, lat)` after the antimeridian fix."""
    norm_lon = ((lon + 180) % 360 + 360) % 360 - 180
    if norm_lon == -180 and lon > -180:
        norm_lon = 180
    return norm_lon + 180, 90 - lat


def test_project_keeps_positive_antimeridian_at_the_right_edge() -> None:
    x, _y = _project(180.0, -84.71)

    assert x == 360


def test_project_keeps_negative_antimeridian_at_the_left_edge() -> None:
    x, _y = _project(-180.0, -84.71)

    assert x == 0


def test_project_is_continuous_approaching_the_antimeridian_from_either_side() -> None:
    just_under, _ = _project(179.99, 0.0)
    just_over, _ = _project(-179.99, 0.0)

    assert just_under == pytest.approx(359.99)
    assert just_over == pytest.approx(0.01)


def test_project_leaves_ordinary_longitudes_unchanged() -> None:
    assert _project(0.0, 0.0) == (180, 90)
    assert _project(-45.0, 10.0) == (135, 80)
    assert _project(90.0, -30.0) == (270, 120)


def test_world_json_antimeridian_points_project_to_opposite_edges() -> None:
    """Every point in the real, shipped world.json that sits exactly on the
    antimeridian must project to the correct edge -- this is what actually
    broke in production, not just the formula in isolation."""
    data = json.loads(WORLD_JSON_PATH.read_text(encoding="utf-8"))
    polygons = data["land"] + data["lakes"]

    checked_positive = 0
    checked_negative = 0
    for polygon in polygons:
        for lon, lat in polygon:
            if lon == 180.0:
                x, _y = _project(lon, lat)
                assert x == 360
                checked_positive += 1
            elif lon == -180.0:
                x, _y = _project(lon, lat)
                assert x == 0
                checked_negative += 1

    # world.json is known to contain both (Antarctica's and Eurasia's rings
    # both touch the antimeridian) -- if a future regeneration of world.json
    # no longer does, this would silently stop testing anything, so pin that
    # the fixture data still exercises the bug this test guards against.
    assert checked_positive > 0
    assert checked_negative > 0


def test_world_json_rings_have_no_unexplained_wide_jumps() -> None:
    """Beyond the antimeridian case above, no ring should have consecutive
    points implying a >180 degree raw longitude jump -- that would indicate
    an unclosed or otherwise malformed ring, which would misrender the same
    way regardless of the projection fix."""
    data = json.loads(WORLD_JSON_PATH.read_text(encoding="utf-8"))
    polygons = data["land"] + data["lakes"]

    offending: list[tuple[int, int]] = []
    for i, polygon in enumerate(polygons):
        n = len(polygon)
        for j in range(n):
            lon1, _ = polygon[j]
            lon2, _ = polygon[(j + 1) % n]
            if abs(lon1 - lon2) > 180:
                offending.append((i, j))

    # The one legitimate case: Antarctica's ring dips to the pole at +180
    # and continues at -180 (a deliberate, correct antimeridian crossing,
    # not a data bug) -- see the module docstring.
    assert len(offending) <= 1
