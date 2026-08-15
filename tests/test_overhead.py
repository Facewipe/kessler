"""Tests for overhead-pass computation (kessler.overhead)."""

from __future__ import annotations

import math
import time

import pytest

from kessler.db import SatelliteRecord
from kessler.overhead import (
    OverheadSatellite,
    find_overhead,
    ground_track_distance_km,
    max_ground_range_km,
    topocentric,
)
from kessler.propagate import epoch_datetime, geodetic_to_ecef, position_at, satrec_from_tle
from kessler.screen import CachedSatellite, orbit_range

from .conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2
from .test_screen import CLOSE_NORAD_ID, CLOSE_TLE_LINE1, CLOSE_TLE_LINE2

_SATREC = satrec_from_tle(TEST_TLE_LINE1, TEST_TLE_LINE2)
_EPOCH = epoch_datetime(_SATREC)


def _reference_elevation_deg(
    observer_lat_deg: float,
    observer_lon_deg: float,
    observer_ecef_km: tuple[float, float, float],
    satellite_ecef_km: tuple[float, float, float],
) -> float:
    """Independent reference: elevation as 90 minus the angle between the
    observer's zenith (ellipsoid normal) vector and the line-of-sight
    vector, via plain dot products -- not the SEZ rotation matrix `topocentric` uses.
    """
    lat = math.radians(observer_lat_deg)
    lon = math.radians(observer_lon_deg)
    zenith = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
    los = tuple(s - o for s, o in zip(satellite_ecef_km, observer_ecef_km, strict=True))
    los_norm = math.sqrt(sum(c * c for c in los))
    cos_angle = sum(a * b for a, b in zip(zenith, los, strict=True)) / los_norm
    cos_angle = max(-1.0, min(1.0, cos_angle))
    return 90.0 - math.degrees(math.acos(cos_angle))


def test_topocentric_directly_overhead_is_90_degrees_elevation() -> None:
    """A satellite exactly along the observer's ellipsoid normal (same
    lat/lon, sea level) is, by construction, exactly at the observer's
    zenith: elevation 90 degrees and range equal to the satellite's altitude."""
    position = position_at(_SATREC, _EPOCH)
    observer_ecef_km = geodetic_to_ecef(position.lat_deg, position.lon_deg, 0.0)

    result = topocentric(position.lat_deg, position.lon_deg, observer_ecef_km, position.ecef_km)

    assert result.elevation_deg == pytest.approx(90.0, abs=1e-3)
    assert result.range_km == pytest.approx(position.alt_km, abs=1e-3)


def test_topocentric_due_north_satellite_has_zero_azimuth() -> None:
    """An observer displaced from the sub-satellite point purely in latitude
    (same longitude) sees the satellite due north or due south by exact
    symmetry -- both points lie in the same meridian half-plane, so the SEZ
    East component is exactly zero regardless of the ellipsoid's oblateness."""
    position = position_at(_SATREC, _EPOCH)
    observer_lat_deg = position.lat_deg - 5.0
    observer_lon_deg = position.lon_deg
    observer_ecef_km = geodetic_to_ecef(observer_lat_deg, observer_lon_deg, 0.0)

    result = topocentric(observer_lat_deg, observer_lon_deg, observer_ecef_km, position.ecef_km)

    # 0 and 360 are the same azimuth; floating-point error can land on either side.
    assert min(result.azimuth_deg, 360.0 - result.azimuth_deg) < 1e-6
    reference_elevation = _reference_elevation_deg(
        observer_lat_deg, observer_lon_deg, observer_ecef_km, position.ecef_km
    )
    assert result.elevation_deg == pytest.approx(reference_elevation, abs=1e-6)
    assert 0.0 < result.elevation_deg < 90.0


def test_topocentric_due_south_satellite_has_180_azimuth() -> None:
    position = position_at(_SATREC, _EPOCH)
    observer_lat_deg = position.lat_deg + 5.0
    observer_lon_deg = position.lon_deg
    observer_ecef_km = geodetic_to_ecef(observer_lat_deg, observer_lon_deg, 0.0)

    result = topocentric(observer_lat_deg, observer_lon_deg, observer_ecef_km, position.ecef_km)

    assert result.azimuth_deg == pytest.approx(180.0, abs=1e-6)


def test_ground_track_distance_km_same_point_is_zero() -> None:
    assert ground_track_distance_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)


def test_ground_track_distance_km_equator_quarter_circle() -> None:
    """A quarter of the equator is a well-known distance: (pi/2) * Earth's radius."""
    distance = ground_track_distance_km(0.0, 0.0, 0.0, 90.0)

    assert distance == pytest.approx(math.pi / 2 * 6378.137, rel=1e-3)


def test_max_ground_range_km_is_zero_at_90_degrees_elevation() -> None:
    assert max_ground_range_km(0.0, 500.0, 90.0) == pytest.approx(0.0, abs=1e-9)


def test_max_ground_range_km_shrinks_as_min_elevation_increases() -> None:
    low = max_ground_range_km(0.0, 500.0, 10.0)
    high = max_ground_range_km(0.0, 500.0, 60.0)

    assert low > high > 0.0


def test_find_overhead_includes_satellite_directly_overhead() -> None:
    position = position_at(_SATREC, _EPOCH)
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID,
        name=TEST_SATELLITE_NAME,
        line1=TEST_TLE_LINE1,
        line2=TEST_TLE_LINE2,
    )

    results, truncated = find_overhead(
        [target],
        position.lat_deg,
        position.lon_deg,
        0.0,
        _EPOCH,
        min_elevation_deg=10.0,
        stale_threshold_hours=72.0,
    )

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, OverheadSatellite)
    assert result.norad_id == TEST_NORAD_ID
    assert result.elevation_deg == pytest.approx(90.0, abs=1e-3)
    assert result.range_km == pytest.approx(position.alt_km, abs=1e-3)
    assert result.epoch_age_hours == pytest.approx(0.0, abs=1e-3)
    assert result.stale is False
    assert truncated is False


def test_find_overhead_excludes_satellite_below_the_horizon() -> None:
    """An observer on the opposite side of the Earth from the sub-satellite
    point cannot see it at any positive elevation."""
    position = position_at(_SATREC, _EPOCH)
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID,
        name=TEST_SATELLITE_NAME,
        line1=TEST_TLE_LINE1,
        line2=TEST_TLE_LINE2,
    )
    antipodal_lat = -position.lat_deg
    antipodal_lon = position.lon_deg + 180.0

    results, truncated = find_overhead(
        [target],
        antipodal_lat,
        antipodal_lon,
        0.0,
        _EPOCH,
        min_elevation_deg=10.0,
        stale_threshold_hours=72.0,
    )

    assert results == []
    assert truncated is False


def test_find_overhead_respects_min_elevation_cutoff() -> None:
    """An observer 5 degrees of latitude from the sub-satellite point sees a
    high (but not 90 degree) elevation; raising the cutoff above that
    elevation excludes it, at or below it includes it."""
    position = position_at(_SATREC, _EPOCH)
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID,
        name=TEST_SATELLITE_NAME,
        line1=TEST_TLE_LINE1,
        line2=TEST_TLE_LINE2,
    )
    observer_lat = position.lat_deg - 5.0
    observer_ecef = geodetic_to_ecef(observer_lat, position.lon_deg, 0.0)
    actual = topocentric(observer_lat, position.lon_deg, observer_ecef, position.ecef_km)

    included, _included_truncated = find_overhead(
        [target],
        observer_lat,
        position.lon_deg,
        0.0,
        _EPOCH,
        min_elevation_deg=actual.elevation_deg - 1.0,
        stale_threshold_hours=72.0,
    )
    excluded, _excluded_truncated = find_overhead(
        [target],
        observer_lat,
        position.lon_deg,
        0.0,
        _EPOCH,
        min_elevation_deg=actual.elevation_deg + 1.0,
        stale_threshold_hours=72.0,
    )

    assert [r.norad_id for r in included] == [TEST_NORAD_ID]
    assert excluded == []


def test_find_overhead_sorts_by_elevation_descending() -> None:
    """The target TLE is exactly overhead (90 degrees, the geometric
    maximum), so a second, slightly displaced satellite (`CLOSE_TLE`, a
    fixture engineered in test_screen.py by shifting the target's mean
    anomaly by -0.1 degree) is guaranteed a strictly lower elevation from the
    same observer -- proving `find_overhead` itself sorts a mixed catalog,
    not just picks the right single result."""
    position = position_at(_SATREC, _EPOCH)
    directly_overhead = SatelliteRecord(
        norad_id=TEST_NORAD_ID, name="OVERHEAD", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )
    off_to_the_side = SatelliteRecord(
        norad_id=CLOSE_NORAD_ID, name="LOWER", line1=CLOSE_TLE_LINE1, line2=CLOSE_TLE_LINE2
    )

    # Deliberately catalog the lower one first to prove sorting, not insertion order.
    combined, _truncated = find_overhead(
        [off_to_the_side, directly_overhead],
        position.lat_deg,
        position.lon_deg,
        0.0,
        _EPOCH,
        min_elevation_deg=0.0,
        stale_threshold_hours=72.0,
    )

    assert [r.norad_id for r in combined] == [TEST_NORAD_ID, CLOSE_NORAD_ID]
    assert combined[0].elevation_deg > combined[1].elevation_deg


@pytest.mark.slow
def test_find_overhead_synthetic_16k_catalog_stays_fast() -> None:
    """A catalog-sized (16k) set of satellites, mostly pruned by the coarse
    ground-track filter, screens well under 2 seconds."""
    catalog = [
        SatelliteRecord(
            norad_id=300000 + i,
            name=f"SAT-{i}",
            line1=TEST_TLE_LINE1,
            line2=TEST_TLE_LINE2,
        )
        for i in range(16000)
    ]
    at = _EPOCH

    start = time.perf_counter()
    results, truncated = find_overhead(
        catalog, 51.5074, -0.1278, 0.0, at, min_elevation_deg=10.0, stale_threshold_hours=72.0
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    # All 16000 records share the reference TLE's exact position, so they
    # are either all visible or all pruned together -- either is fine here,
    # this test is about speed, not the visibility outcome.
    assert len(results) in (0, 16000)
    assert truncated is False


def test_find_overhead_uses_catalog_cache_without_reparsing(monkeypatch) -> None:
    """When `catalog_cache` covers every record, `find_overhead` must not
    re-parse TLEs -- that per-request re-parsing of the whole catalog was a
    large, entirely avoidable share of `/overhead`'s cost."""
    target = SatelliteRecord(
        norad_id=TEST_NORAD_ID,
        name=TEST_SATELLITE_NAME,
        line1=TEST_TLE_LINE1,
        line2=TEST_TLE_LINE2,
    )
    position = position_at(_SATREC, _EPOCH)
    catalog_cache = {
        target.norad_id: CachedSatellite(
            record=target, satrec=_SATREC, orbit_range=orbit_range(_SATREC)
        ),
    }

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("satrec_from_tle must not be called when catalog_cache is complete")

    monkeypatch.setattr("kessler.overhead.satrec_from_tle", _fail_if_called)

    results, truncated = find_overhead(
        [target],
        position.lat_deg,
        position.lon_deg,
        0.0,
        _EPOCH,
        min_elevation_deg=10.0,
        stale_threshold_hours=72.0,
        catalog_cache=catalog_cache,
    )

    assert len(results) == 1
    assert truncated is False


def test_find_overhead_time_budget_truncates_instead_of_hanging() -> None:
    catalog = [
        SatelliteRecord(
            norad_id=400000 + i, name=f"SAT-{i}", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
        )
        for i in range(1000)
    ]

    results, truncated = find_overhead(
        catalog,
        51.5074,
        -0.1278,
        0.0,
        _EPOCH,
        min_elevation_deg=10.0,
        stale_threshold_hours=72.0,
        time_budget_seconds=0.0,
    )

    assert results == []
    assert truncated is True
