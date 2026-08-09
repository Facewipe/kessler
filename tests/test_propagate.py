"""Tests for SGP4-based orbit propagation."""

from datetime import datetime, timezone

import pytest

from kessler.propagate import epoch_datetime, position_at, satrec_from_tle

# SGP4 validation test satellite from Vallado, Crawford, Hujsak & Kelso,
# "Revisiting Spacetrack Report #3" (2006).
LINE1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"

# Reference TEME state vector for this TLE at its own epoch (tsince = 0.0
# min), as published in the SGP4 validation test suite. This is the direct
# sgp4 output, independent of our TEME -> geodetic conversion.
EXPECTED_TEME_KM = (7022.46529266, -1400.08296755, 0.03995155)


def test_epoch_datetime_matches_tle_epoch_field() -> None:
    satrec = satrec_from_tle(LINE1, LINE2)

    epoch = epoch_datetime(satrec)
    expected = datetime(2000, 6, 27, 18, 50, 19, 733568, tzinfo=timezone.utc)

    assert abs((epoch - expected).total_seconds()) < 1e-3


def test_position_at_epoch_matches_reference_teme_vector() -> None:
    satrec = satrec_from_tle(LINE1, LINE2)
    at = epoch_datetime(satrec)

    position = position_at(satrec, at)

    for actual, expected in zip(position.teme_km, EXPECTED_TEME_KM, strict=True):
        assert actual == pytest.approx(expected, abs=0.1)


def test_position_at_epoch_geodetic_is_precomputed_within_tolerance() -> None:
    satrec = satrec_from_tle(LINE1, LINE2)
    at = epoch_datetime(satrec)

    position = position_at(satrec, at)

    assert -90.0 <= position.lat_deg <= 90.0
    assert -180.0 <= position.lon_deg <= 180.0
    # At epoch the satellite is essentially crossing the equatorial plane
    # (TEME z ~= 40 m), and independently derived (by hand, from the
    # reference TEME vector above via GMST + WGS84 conversion) lat/lon/alt.
    assert abs(position.lat_deg) < 0.1
    assert position.lon_deg == pytest.approx(149.96, abs=1.0)
    assert position.alt_km == pytest.approx(782.3, abs=5.0)


def test_position_at_treats_naive_datetime_as_utc() -> None:
    satrec = satrec_from_tle(LINE1, LINE2)
    at = epoch_datetime(satrec).replace(tzinfo=None)

    naive_position = position_at(satrec, at)
    aware_position = position_at(satrec, epoch_datetime(satrec))

    assert naive_position.teme_km == aware_position.teme_km
