"""Tests for conjunction screening (kessler.screen)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from kessler.db import SatelliteRecord
from kessler.propagate import epoch_datetime, position_at, satrec_from_tle
from kessler.screen import (
    OrbitRange,
    find_close_approaches,
    orbit_range,
    ranges_overlap,
    screen_catalog,
)

from .conftest import TEST_NORAD_ID, TEST_TLE_LINE1, TEST_TLE_LINE2

# Fixtures below are engineered from the reference SGP4 validation TLE
# (see conftest.py) by hand-editing individual TLE fields and recomputing the
# line's checksum, so their orbital behavior near epoch is predictable.

# Same orbital plane and shape as the reference TLE, with mean anomaly
# shifted by -0.1 degree. Two objects on the same orbit, phase-shifted by a
# small, constant angle, stay a roughly constant *time* apart (mean anomaly
# is linear in time by construction) but their *separation in space* still
# oscillates once per orbit, tracking orbital speed -- widest near perigee,
# narrowest near apogee. The reference TLE's epoch is ~7 minutes past its own
# perigee, so its apogee (a real, interior local minimum of separation, not
# just "wherever we started looking") falls at epoch + ~59-60 minutes.
CLOSE_NORAD_ID = 100005
CLOSE_TLE_LINE1 = TEST_TLE_LINE1
CLOSE_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.2264 10.82419157413666"

# Same construction, mean anomaly shifted by -0.2 degree: a second engineered
# close approach, roughly twice as far as CLOSE_TLE at closest approach.
CLOSE2_NORAD_ID = 100006
CLOSE2_TLE_LINE1 = TEST_TLE_LINE1
CLOSE2_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.1264 10.82419157413665"

# Same TLE with mean motion swapped to a ~1 rev/day (GEO-like) period. Its
# derived semi-major axis (~42,000 km) puts it far outside the reference
# satellite's LEO altitude band, regardless of the (unmodified) eccentricity.
FAR_NORAD_ID = 200005
FAR_TLE_LINE1 = TEST_TLE_LINE1
FAR_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 01.00273790413668"

_EPOCH = epoch_datetime(satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2))

# A 30-minute window straddling the post-epoch apogee crossing, so the
# engineered pairs' separation has a genuine interior local minimum inside
# the window (not just a monotonic slope from wherever the window happens to
# start or end).
_WINDOW_START = _EPOCH + timedelta(minutes=45)
_WINDOW_END = _EPOCH + timedelta(minutes=75)


def _brute_force_closest(satrec_a, satrec_b, start, end, step_seconds=1.0):
    """Independent reference closest-approach search: a dumb linear scan."""
    total_steps = int((end - start).total_seconds() / step_seconds)
    best_time = start
    best_distance = None
    for i in range(total_steps + 1):
        t = start + timedelta(seconds=i * step_seconds)
        pos_a = position_at(satrec_a, t)
        pos_b = position_at(satrec_b, t)
        dx = pos_a.teme_km[0] - pos_b.teme_km[0]
        dy = pos_a.teme_km[1] - pos_b.teme_km[1]
        dz = pos_a.teme_km[2] - pos_b.teme_km[2]
        distance = (dx * dx + dy * dy + dz * dz) ** 0.5
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_time = t
    return best_time, best_distance


def test_ranges_overlap_true_when_ranges_intersect() -> None:
    a = OrbitRange(perigee_km=400, apogee_km=800)
    b = OrbitRange(perigee_km=750, apogee_km=1200)

    assert ranges_overlap(a, b, buffer_km=0)


def test_ranges_overlap_false_when_disjoint_even_with_buffer() -> None:
    leo = OrbitRange(perigee_km=400, apogee_km=800)
    geo = OrbitRange(perigee_km=35786, apogee_km=35786)

    assert not ranges_overlap(leo, geo, buffer_km=50)


def test_orbit_range_matches_reference_tle_leo_band() -> None:
    satrec = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)

    result = orbit_range(satrec)

    assert result.perigee_km < result.apogee_km
    assert 200 < result.perigee_km < 2000
    assert 500 < result.apogee_km < 8000


def test_find_close_approaches_matches_brute_force_reference() -> None:
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    other = satrec_from_tle(CLOSE_TLE_LINE1, CLOSE_TLE_LINE2)
    start, end = _WINDOW_START, _WINDOW_END

    expected_time, expected_distance = _brute_force_closest(target, other, start, end)
    # Sanity check the fixture is actually engineered for a close pass, well
    # under the search bound below and orbital scale (radius ~7000 km).
    assert expected_distance < 100.0

    candidates = find_close_approaches(target, other, start, end, candidate_bound_km=100.0)

    assert candidates
    best = min(candidates, key=lambda c: c.miss_distance_km)
    assert abs((best.tca - expected_time).total_seconds()) <= 60
    assert best.miss_distance_km == pytest.approx(expected_distance, abs=0.5)


def test_screen_catalog_prunes_non_overlapping_orbit() -> None:
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    close = SatelliteRecord(
        norad_id=CLOSE_NORAD_ID, name="CLOSE", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
    )
    far = SatelliteRecord(
        norad_id=FAR_NORAD_ID, name="FAR", line1=FAR_TLE_LINE1, line2=FAR_TLE_LINE2
    )
    start, end = _WINDOW_START, _WINDOW_END

    results = screen_catalog(target, [close, far], start, end, threshold_km=100.0)

    assert [r.other_norad_id for r in results] == [CLOSE_NORAD_ID]
    assert results[0].miss_distance_km < 100.0
    assert results[0].target_epoch_age_hours == pytest.approx(0.75, abs=0.01)


def test_screen_catalog_sorts_results_by_miss_distance() -> None:
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    close = SatelliteRecord(
        norad_id=CLOSE_NORAD_ID, name="CLOSE", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
    )
    close2 = SatelliteRecord(
        norad_id=CLOSE2_NORAD_ID, name="CLOSE2", line1=CLOSE2_TLE_LINE1, line2=CLOSE2_TLE_LINE2
    )
    start, end = _WINDOW_START, _WINDOW_END

    # Deliberately catalog the farther one first to prove sorting, not insertion order.
    results = screen_catalog(target, [close2, close], start, end, threshold_km=100.0)

    assert [r.other_norad_id for r in results] == [CLOSE_NORAD_ID, CLOSE2_NORAD_ID]
    assert results[0].miss_distance_km <= results[1].miss_distance_km


@pytest.mark.slow
def test_screen_catalog_synthetic_fleet_stays_fast() -> None:
    """A larger synthetic catalog, entirely pruned by the coarse filter, screens quickly."""
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    catalog = [
        SatelliteRecord(
            norad_id=FAR_NORAD_ID + i, name=f"FAR-{i}", line1=FAR_TLE_LINE1, line2=FAR_TLE_LINE2
        )
        for i in range(50)
    ]
    start = _EPOCH
    end = _EPOCH + timedelta(hours=72)

    results = screen_catalog(target, catalog, start, end, threshold_km=10.0)

    assert results == []
