"""Tests for kessler.catalog_cache."""

from __future__ import annotations

from kessler.catalog_cache import get_cached_catalog
from kessler.db import SatelliteRecord

from .conftest import TEST_NORAD_ID, TEST_TLE_LINE1, TEST_TLE_LINE2


def _record(norad_id: int) -> SatelliteRecord:
    return SatelliteRecord(
        norad_id=norad_id, name=f"SAT-{norad_id}", line1=TEST_TLE_LINE1, line2=TEST_TLE_LINE2
    )


def test_get_cached_catalog_builds_entry_per_record() -> None:
    catalog = [_record(TEST_NORAD_ID), _record(TEST_NORAD_ID + 1)]

    cache = get_cached_catalog(catalog)

    assert set(cache) == {TEST_NORAD_ID, TEST_NORAD_ID + 1}
    for norad_id, cached in cache.items():
        assert cached.record.norad_id == norad_id
        assert cached.satrec is not None
        assert cached.orbit_range.perigee_km < cached.orbit_range.apogee_km


def test_get_cached_catalog_reuses_unchanged_catalog() -> None:
    catalog = [_record(TEST_NORAD_ID)]

    first = get_cached_catalog(catalog)
    second = get_cached_catalog(list(catalog))  # same content, different list object

    assert first is second


def test_get_cached_catalog_rebuilds_when_catalog_changes() -> None:
    first = get_cached_catalog([_record(TEST_NORAD_ID)])
    second = get_cached_catalog([_record(TEST_NORAD_ID), _record(TEST_NORAD_ID + 1)])

    assert first is not second
    assert set(second) == {TEST_NORAD_ID, TEST_NORAD_ID + 1}
