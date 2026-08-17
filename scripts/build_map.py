#!/usr/bin/env python3
"""Build kessler/static/world.json from Natural Earth 110m land/lakes data.

Downloads the public-domain Natural Earth ``ne_110m_land`` and
``ne_110m_lakes`` shapefiles, simplifies each ring with Douglas-Peucker,
rounds coordinates to 2 decimals, and writes a small JSON file of land and
lake polygons that the demo page loads at startup instead of drawing
coastlines by hand.

``ne_110m_land`` has no interior rings cut out for large inland water
bodies (the Great Lakes, Lake Baikal, Lake Victoria, ...), so on their
own the land polygons render them as land. The lakes shapefile is fetched
separately and shipped as its own polygon list, drawn in the ocean colour
on top of the land layer, rather than trying to punch real holes in the
land polygons.

Note: Natural Earth classifies the Caspian Sea as a sea, not a lake, so
it is *not* in ``ne_110m_lakes`` and still renders as land here -- fixing
that would mean pulling it in from a different dataset
(``ne_110m_geography_marine_polys``), which this script deliberately
doesn't do, to keep this to one straightforward "fetch another
shapefile, draw it over the land" step.

Usage:
    python scripts/build_map.py

No third-party dependencies: the shapefiles are parsed directly from their
binary format (see the ESRI Shapefile Technical Description) since we
only need polygon rings, not the full feature set a library like
pyshp/geopandas would give us.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

NE_LAND_URL = "https://naturalearth.s3.amazonaws.com/110m_physical/ne_110m_land.zip"
NE_LAKES_URL = "https://naturalearth.s3.amazonaws.com/110m_physical/ne_110m_lakes.zip"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "kessler" / "static" / "world.json"

# Raised from the land-only 150 KB to cover the added lakes data (~15 KB at
# the initial tolerance -- ne_110m_lakes is already coarse, only the
# largest lakes are present at 110m resolution).
SIZE_BUDGET_BYTES = 180 * 1024

# Starting Douglas-Peucker tolerance in degrees; doubled until the output
# fits the size budget.
INITIAL_TOLERANCE_DEG = 0.05
MAX_TOLERANCE_DEG = 2.0

SHAPE_TYPE_POLYGON = 5


def download_shapefile_bytes(url: str) -> bytes:
    """Download the Natural Earth zip and return the raw .shp file bytes."""
    with urllib.request.urlopen(url, timeout=30) as response:
        archive_bytes = response.read()

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        shp_names = [name for name in archive.namelist() if name.lower().endswith(".shp")]
        if not shp_names:
            raise ValueError(f"no .shp file found in archive from {url}")
        return archive.read(shp_names[0])


def parse_polygon_rings(shp_bytes: bytes) -> list[list[tuple[float, float]]]:
    """Parse an ESRI shapefile of Polygon features into a list of rings.

    Each ring is a list of (lon, lat) tuples. Ring winding (outer vs hole)
    is not distinguished: neither ne_110m_land nor ne_110m_lakes has
    interior rings, so every ring is a standalone filled shape.
    """
    rings: list[list[tuple[float, float]]] = []
    offset = 100  # fixed-length file header

    while offset < len(shp_bytes):
        _record_number, content_length_words = struct.unpack_from(">ii", shp_bytes, offset)
        record_start = offset + 8
        content_length_bytes = content_length_words * 2
        (shape_type,) = struct.unpack_from("<i", shp_bytes, record_start)

        if shape_type == SHAPE_TYPE_POLYGON:
            num_parts, num_points = struct.unpack_from("<ii", shp_bytes, record_start + 36)
            parts_offset = record_start + 44
            parts = struct.unpack_from(f"<{num_parts}i", shp_bytes, parts_offset)
            points_offset = parts_offset + 4 * num_parts
            points = struct.unpack_from(f"<{2 * num_points}d", shp_bytes, points_offset)
            coords = list(zip(points[0::2], points[1::2], strict=True))

            for part_index, start in enumerate(parts):
                end = parts[part_index + 1] if part_index + 1 < len(parts) else num_points
                rings.append(coords[start:end])

        offset = record_start + content_length_bytes

    return rings


def perpendicular_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Distance from `point` to the line segment `start`-`end`."""
    if start == end:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5

    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = (dx**2 + dy**2) ** 0.5
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / norm


def simplify_ring(
    ring: list[tuple[float, float]], tolerance_deg: float
) -> list[tuple[float, float]]:
    """Simplify a ring with the Douglas-Peucker algorithm."""
    if len(ring) < 3:
        return ring

    max_distance = 0.0
    split_index = 0
    for i in range(1, len(ring) - 1):
        distance = perpendicular_distance(ring[i], ring[0], ring[-1])
        if distance > max_distance:
            max_distance = distance
            split_index = i

    if max_distance <= tolerance_deg:
        return [ring[0], ring[-1]]

    left = simplify_ring(ring[: split_index + 1], tolerance_deg)
    right = simplify_ring(ring[split_index:], tolerance_deg)
    return left[:-1] + right


def simplify_and_round(
    rings: list[list[tuple[float, float]]], tolerance_deg: float
) -> list[list[list[float]]]:
    """Simplify and round rings into JSON-ready polygons."""
    polygons = []
    for ring in rings:
        simplified = simplify_ring(ring, tolerance_deg)
        if len(simplified) < 3:
            continue
        polygons.append([[round(lon, 2), round(lat, 2)] for lon, lat in simplified])
    return polygons


def build_world_json(
    land_rings: list[list[tuple[float, float]]],
    lake_rings: list[list[tuple[float, float]]],
    tolerance_deg: float,
) -> bytes:
    """Simplify and round both ring sets, returning the encoded JSON bytes."""
    data = {
        "land": simplify_and_round(land_rings, tolerance_deg),
        "lakes": simplify_and_round(lake_rings, tolerance_deg),
    }
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def main() -> int:
    try:
        print(f"Downloading {NE_LAND_URL} ...")
        land_shp_bytes = download_shapefile_bytes(NE_LAND_URL)
        print(f"Downloading {NE_LAKES_URL} ...")
        lake_shp_bytes = download_shapefile_bytes(NE_LAKES_URL)
    except OSError as exc:
        print(f"error: could not download Natural Earth data: {exc}", file=sys.stderr)
        return 1

    land_rings = parse_polygon_rings(land_shp_bytes)
    lake_rings = parse_polygon_rings(lake_shp_bytes)
    print(f"Parsed {len(land_rings)} land rings and {len(lake_rings)} lake rings.")

    tolerance_deg = INITIAL_TOLERANCE_DEG
    encoded = build_world_json(land_rings, lake_rings, tolerance_deg)
    while len(encoded) > SIZE_BUDGET_BYTES and tolerance_deg < MAX_TOLERANCE_DEG:
        tolerance_deg *= 2
        encoded = build_world_json(land_rings, lake_rings, tolerance_deg)

    OUTPUT_PATH.write_bytes(encoded)
    print(f"Wrote {OUTPUT_PATH} ({len(encoded) / 1024:.1f} KB, tolerance={tolerance_deg:.3f} deg)")

    if len(encoded) > SIZE_BUDGET_BYTES:
        print(
            f"warning: output is over the {SIZE_BUDGET_BYTES / 1024:.0f} KB budget",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
