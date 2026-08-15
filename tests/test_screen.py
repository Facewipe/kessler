"""Tests for conjunction screening (kessler.screen)."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from kessler.db import SatelliteRecord
from kessler.propagate import epoch_datetime, position_at, satrec_from_tle
from kessler.screen import (
    EPOCH_AGE_DRIFT_KM_PER_HOUR,
    CachedSatellite,
    OrbitRange,
    colocation_bound_km,
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

# Same exact TLE as the target: models a docked vehicle or a station's own
# module, which shares the station's orbit and therefore stays at ~0 km
# separation for the whole window -- not a conjunction.
DOCKED_NORAD_ID = 100007

# Same orbit as the reference TLE (same epoch, same mean anomaly), but with
# mean motion increased by 0.0015 rev/day. This models a docked vehicle
# whose independently-fit TLE has a slightly different mean motion from the
# station's -- the mechanism reported in issue #22, where a 44h-old vs. 7h-
# old TLE pair for physically docked spacecraft drifted apart under
# propagation enough to clear the 1 km co-location bound and get reported as
# a spurious conjunction. By vis-viva, this mean-motion delta corresponds to
# under 1 m/s of along-track drift (three orders of magnitude below LEO
# orbital speed, consistent with two objects that are not actually
# separating) yet accumulates past a 1 km position bound within roughly the
# first half hour of propagation.
DOCKED_DRIFT_NORAD_ID = 100008
DOCKED_DRIFT_TLE_LINE1 = TEST_TLE_LINE1
DOCKED_DRIFT_TLE_LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82569157413663"

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


def test_find_close_approaches_tca_strictly_inside_window() -> None:
    """A genuine interior close approach must not degenerate to the window edge.

    Regression test: refinement previously returned the first grid point
    (the window start) instead of the true local minimum in some cases.
    """
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    other = satrec_from_tle(CLOSE_TLE_LINE1, CLOSE_TLE_LINE2)
    start, end = _WINDOW_START, _WINDOW_END

    candidates = find_close_approaches(target, other, start, end, candidate_bound_km=100.0)

    assert candidates
    best = min(candidates, key=lambda c: c.miss_distance_km)
    assert start < best.tca < end


def test_find_close_approaches_excludes_colocated_objects() -> None:
    """Objects sharing an orbit (docked spacecraft, a station's own modules)
    stay at ~0 km separation for the whole window and are not a conjunction."""
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    docked = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    start, end = _WINDOW_START, _WINDOW_END

    candidates = find_close_approaches(
        target, docked, start, end, candidate_bound_km=100.0, min_separation_km=1.0
    )

    assert candidates == []


def test_find_close_approaches_min_separation_km_is_configurable() -> None:
    """The co-location threshold is a real parameter, not a hardcoded value."""
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    docked = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    start, end = _WINDOW_START, _WINDOW_END

    excluded = find_close_approaches(
        target, docked, start, end, candidate_bound_km=100.0, min_separation_km=1.0
    )
    included = find_close_approaches(
        target, docked, start, end, candidate_bound_km=100.0, min_separation_km=-1.0
    )

    assert excluded == []
    assert included != []


def test_find_close_approaches_epoch_drift_exceeds_fixed_bound() -> None:
    """Reproduces the reported bug (issue #22): a docked object whose
    independently-fit TLE has a slightly different mean motion (as real
    epoch-mismatched TLEs do -- see DOCKED_DRIFT_TLE_LINE2 above) drifts
    past a fixed 1 km co-location bound well within the screening window,
    even though it never actually separates from the target. A long window
    is used so the drift clears 1 km with a wide margin regardless of the
    exact drift rate."""
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    drifting = satrec_from_tle(DOCKED_DRIFT_TLE_LINE1, DOCKED_DRIFT_TLE_LINE2)
    start, end = _EPOCH, _EPOCH + timedelta(hours=24)

    candidates = find_close_approaches(
        target, drifting, start, end, candidate_bound_km=200.0, min_separation_km=1.0
    )

    assert candidates != []


def test_find_close_approaches_wider_bound_excludes_epoch_drifted_pair() -> None:
    """A bound wide enough to cover the propagation drift -- what
    `colocation_bound_km` provides once it accounts for a real epoch-age gap
    -- correctly treats the same pair as co-located instead of a
    conjunction. A short window is used so the drift stays a small fraction
    of the bound regardless of the exact drift rate."""
    target = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
    drifting = satrec_from_tle(DOCKED_DRIFT_TLE_LINE1, DOCKED_DRIFT_TLE_LINE2)
    start, end = _EPOCH, _EPOCH + timedelta(minutes=10)

    candidates = find_close_approaches(
        target, drifting, start, end, candidate_bound_km=100.0, min_separation_km=50.0
    )

    assert candidates == []


def test_colocation_bound_km_widens_with_epoch_age_gap() -> None:
    assert colocation_bound_km(1.0, 0.0) == pytest.approx(1.0)
    assert colocation_bound_km(1.0, 37.0) == pytest.approx(1.0 + EPOCH_AGE_DRIFT_KM_PER_HOUR * 37.0)


def test_colocation_bound_km_ignores_sign_of_epoch_age_gap() -> None:
    assert colocation_bound_km(1.0, -37.0) == colocation_bound_km(1.0, 37.0)


def test_screen_catalog_excludes_colocated_pair() -> None:
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    docked = SatelliteRecord(
        norad_id=DOCKED_NORAD_ID,
        name="DOCKED-MODULE",
        line1=TEST_TLE_LINE1,
        line2=TEST_TLE_LINE2,
    )
    start, end = _WINDOW_START, _WINDOW_END

    results, truncated = screen_catalog(target, [docked], start, end, threshold_km=100.0)

    assert results == []
    assert truncated is False


def test_screen_catalog_excludes_drifted_docked_pair_given_enough_margin() -> None:
    """End-to-end: `screen_catalog` wires `min_separation_km` through to
    `find_close_approaches` and treats a still-drifting-but-physically-docked
    pair (see DOCKED_DRIFT_TLE_LINE2 above) as co-located once the bound
    covers the drift -- what `colocation_bound_km` provides automatically
    for a pair with a real epoch-age gap."""
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    drifting = SatelliteRecord(
        norad_id=DOCKED_DRIFT_NORAD_ID,
        name="DOCKED-DRIFT",
        line1=DOCKED_DRIFT_TLE_LINE1,
        line2=DOCKED_DRIFT_TLE_LINE2,
    )
    start, end = _EPOCH, _EPOCH + timedelta(minutes=10)

    results, truncated = screen_catalog(
        target, [drifting], start, end, threshold_km=100.0, min_separation_km=50.0
    )

    assert results == []
    assert truncated is False


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

    results, truncated = screen_catalog(target, [close, far], start, end, threshold_km=100.0)

    assert [r.other_norad_id for r in results] == [CLOSE_NORAD_ID]
    assert results[0].miss_distance_km < 100.0
    assert results[0].target_epoch_age_hours == pytest.approx(0.75, abs=0.01)
    assert truncated is False


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
    results, _truncated = screen_catalog(target, [close2, close], start, end, threshold_km=100.0)

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

    results, truncated = screen_catalog(target, catalog, start, end, threshold_km=10.0)

    assert results == []
    assert truncated is False


def test_screen_catalog_uses_catalog_cache_without_reparsing(monkeypatch) -> None:
    """When `catalog_cache` covers every record, `screen_catalog` must not
    re-parse TLEs or re-derive orbit ranges -- that per-request re-parsing
    of the whole catalog was a large, entirely avoidable share of
    `/conjunctions`'s cost (see `kessler.catalog_cache`)."""
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    close = SatelliteRecord(
        norad_id=CLOSE_NORAD_ID, name="CLOSE", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
    )
    start, end = _WINDOW_START, _WINDOW_END

    target_satrec = satrec_from_tle(target.line1, target.line2)
    close_satrec = satrec_from_tle(close.line1, close.line2)
    catalog_cache = {
        target.norad_id: CachedSatellite(
            record=target, satrec=target_satrec, orbit_range=orbit_range(target_satrec)
        ),
        close.norad_id: CachedSatellite(
            record=close, satrec=close_satrec, orbit_range=orbit_range(close_satrec)
        ),
    }

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("satrec_from_tle must not be called when catalog_cache is complete")

    monkeypatch.setattr("kessler.screen.satrec_from_tle", _fail_if_called)

    results, truncated = screen_catalog(
        target, [close], start, end, threshold_km=100.0, catalog_cache=catalog_cache
    )

    assert [r.other_norad_id for r in results] == [CLOSE_NORAD_ID]
    assert truncated is False


@pytest.mark.slow
def test_screen_catalog_time_budget_bounds_wall_clock_time() -> None:
    """The production failure mode this is fixing: a catalog with many
    entries sharing the target's orbit is not pruned by the coarse filter
    at all, so every one of them is a genuine coarse+fine propagation
    candidate over the full window -- without a hard budget this runs for
    as long as the catalog demands, which is exactly what made
    `/conjunctions` hang. With a budget, wall-clock time must stay bounded
    and the response must come back flagged as truncated.
    """
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="TARGET", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    # Same orbit as the target -- entirely unpruned by the coarse
    # orbit-overlap filter, so every entry needs full propagation.
    catalog = [
        SatelliteRecord(
            norad_id=CLOSE_NORAD_ID + 1 + i,
            name=f"OVERLAP-{i}",
            line1=CLOSE_TLE_LINE1,
            line2=CLOSE_TLE_LINE2,
        )
        for i in range(500)
    ]
    start = _EPOCH
    end = _EPOCH + timedelta(hours=72)
    budget_seconds = 1.0

    started = time.monotonic()
    results, truncated = screen_catalog(
        target, catalog, start, end, threshold_km=10.0, time_budget_seconds=budget_seconds
    )
    elapsed = time.monotonic() - started

    # A generous margin over the budget: the deadline is checked between
    # coarse-grid samples, not preemptively, so some small overshoot is
    # expected -- but nowhere near the ~19s an untruncated run over this
    # catalog would take (measured empirically at ~38ms/pair).
    assert elapsed < budget_seconds + 3.0
    assert truncated is True
