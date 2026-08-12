"""Overhead-pass computation: which catalog satellites are above an observer's horizon.

For each catalog satellite:

1. Propagate to `at` and read off its geodetic sub-point (lat/lon/alt) --
   unavoidable per satellite, since visibility depends on where it actually
   is.
2. Coarse ground-track filter: compare the great-circle distance between the
   observer and the satellite's sub-point against the maximum ground range
   at which a satellite at that altitude could possibly be visible at
   `min_elevation_deg` (see `max_ground_range_km`). This is a cheap haversine
   check that discards the vast majority of a large catalog.
3. Only satellites surviving the coarse filter get the full topocentric
   conversion (ECEF range vector -> SEZ frame -> elevation/azimuth/range),
   which is the more expensive step this filter exists to avoid repeating
   for the whole catalog.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from kessler.db import SatelliteRecord
from kessler.propagate import (
    PropagationError,
    epoch_datetime,
    geodetic_to_ecef,
    position_at,
    satrec_from_tle,
)

_EARTH_RADIUS_KM = 6378.137

DEFAULT_MIN_ELEVATION_DEG = 10.0

# Safety margin added to `max_ground_range_km` before applying it as a prune
# cutoff, to absorb the difference between that formula's spherical-Earth
# approximation and the WGS84 ellipsoid used everywhere else, so the coarse
# filter never discards a satellite that the full topocentric conversion
# would have reported as visible.
_GROUND_RANGE_BUFFER_KM = 50.0


@dataclass(frozen=True)
class Topocentric:
    """A satellite's look angles as seen from a ground observer."""

    elevation_deg: float
    azimuth_deg: float
    range_km: float


@dataclass(frozen=True)
class OverheadSatellite:
    """One catalog satellite currently above an observer's horizon."""

    norad_id: int
    name: str
    elevation_deg: float
    azimuth_deg: float
    range_km: float
    alt_km: float
    epoch_age_hours: float
    stale: bool


def max_ground_range_km(
    observer_alt_km: float, satellite_alt_km: float, min_elevation_deg: float
) -> float:
    """Maximum great-circle ground distance (km) at which a satellite at
    `satellite_alt_km` altitude could be seen at or above `min_elevation_deg`
    from an observer at `observer_alt_km` altitude.

    Derived from the law of sines on the triangle formed by Earth's center,
    the observer, and the satellite: the interior angle at the observer is
    90 + elevation, since the local horizon is perpendicular to the
    observer's radius vector. Uses a spherical-Earth approximation (mean
    equatorial radius), which is adequate for a coarse prune -- see
    `_GROUND_RANGE_BUFFER_KM`.
    """
    r_obs = _EARTH_RADIUS_KM + observer_alt_km
    r_sat = _EARTH_RADIUS_KM + satellite_alt_km
    el = math.radians(min_elevation_deg)

    sin_nadir = max(-1.0, min(1.0, (r_obs / r_sat) * math.cos(el)))
    nadir = math.asin(sin_nadir)
    central_angle = math.pi / 2 - el - nadir
    if central_angle <= 0:
        return 0.0
    return _EARTH_RADIUS_KM * central_angle


def ground_track_distance_km(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> float:
    """Great-circle distance (km) between two lat/lon points via the haversine formula."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1_deg, lon1_deg, lat2_deg, lon2_deg))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def topocentric(
    observer_lat_deg: float,
    observer_lon_deg: float,
    observer_ecef_km: tuple[float, float, float],
    satellite_ecef_km: tuple[float, float, float],
) -> Topocentric:
    """Elevation/azimuth/range of a satellite as seen from an observer.

    Rotates the ECEF range vector into the observer's topocentric
    South-East-Zenith (SEZ) frame (Vallado, *Fundamentals of Astrodynamics
    and Applications*). Azimuth is measured clockwise from North (0-360).
    """
    lat = math.radians(observer_lat_deg)
    lon = math.radians(observer_lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    dx = satellite_ecef_km[0] - observer_ecef_km[0]
    dy = satellite_ecef_km[1] - observer_ecef_km[1]
    dz = satellite_ecef_km[2] - observer_ecef_km[2]

    s = sin_lat * cos_lon * dx + sin_lat * sin_lon * dy - cos_lat * dz
    e = -sin_lon * dx + cos_lon * dy
    z = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

    range_km = math.sqrt(s * s + e * e + z * z)
    elevation_deg = math.degrees(math.asin(z / range_km))
    azimuth_deg = math.degrees(math.atan2(e, -s)) % 360.0

    return Topocentric(elevation_deg=elevation_deg, azimuth_deg=azimuth_deg, range_km=range_km)


def find_overhead(
    catalog: Iterable[SatelliteRecord],
    observer_lat_deg: float,
    observer_lon_deg: float,
    observer_alt_km: float,
    at: datetime,
    min_elevation_deg: float,
    stale_threshold_hours: float,
) -> list[OverheadSatellite]:
    """Return catalog satellites above the observer's horizon at `at`.

    Sorted by elevation, descending. Satellites whose TLE fails to propagate
    to `at` are silently skipped, consistent with `screen_catalog`.
    """
    observer_ecef_km = geodetic_to_ecef(observer_lat_deg, observer_lon_deg, observer_alt_km)

    results: list[OverheadSatellite] = []
    for record in catalog:
        satrec = satrec_from_tle(record.line1, record.line2)
        try:
            position = position_at(satrec, at)
        except PropagationError:
            continue

        ground_distance_km = ground_track_distance_km(
            observer_lat_deg, observer_lon_deg, position.lat_deg, position.lon_deg
        )
        prune_cutoff_km = (
            max_ground_range_km(observer_alt_km, position.alt_km, min_elevation_deg)
            + _GROUND_RANGE_BUFFER_KM
        )
        if ground_distance_km > prune_cutoff_km:
            continue

        topo = topocentric(observer_lat_deg, observer_lon_deg, observer_ecef_km, position.ecef_km)
        if topo.elevation_deg < min_elevation_deg:
            continue

        epoch_age_hours = (at - epoch_datetime(satrec)).total_seconds() / 3600
        results.append(
            OverheadSatellite(
                norad_id=record.norad_id,
                name=record.name,
                elevation_deg=topo.elevation_deg,
                azimuth_deg=topo.azimuth_deg,
                range_km=topo.range_km,
                alt_km=position.alt_km,
                epoch_age_hours=epoch_age_hours,
                stale=epoch_age_hours > stale_threshold_hours,
            )
        )

    results.sort(key=lambda r: r.elevation_deg, reverse=True)
    return results
