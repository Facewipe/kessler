"""SGP4-based orbit propagation helpers.

Positions are computed in the TEME (True Equator, Mean Equinox) frame produced
directly by SGP4, then rotated into ECEF using Greenwich Mean Sidereal Time
(IAU-82 formula) and converted to WGS84 geodetic latitude/longitude/altitude.
Polar motion and precession/nutation are ignored, which is consistent with the
km-level accuracy SGP4 itself provides near a TLE's epoch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sgp4.api import Satrec, jday

# WGS84 ellipsoid parameters, used for the ECEF -> geodetic conversion.
_WGS84_A_KM = 6378.137
_WGS84_F = 1 / 298.257223563
_WGS84_E2 = _WGS84_F * (2 - _WGS84_F)

_J2000_EPOCH = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
_J2000_JD = 2451545.0

_SGP4_ERRORS = {
    1: "mean elements, ecc >= 1.0 or ecc < -0.001 or a < 0.95 er",
    2: "mean motion less than 0.0",
    3: "pert elements, ecc < 0.0 or ecc > 1.0",
    4: "semi-latus rectum < 0.0",
    5: "epoch elements are sub-orbital",
    6: "satellite has decayed",
}


class PropagationError(RuntimeError):
    """Raised when SGP4 fails to propagate a TLE to the requested time."""


@dataclass(frozen=True)
class Position:
    """A satellite's position at a single instant."""

    lat_deg: float
    lon_deg: float
    alt_km: float
    teme_km: tuple[float, float, float]
    ecef_km: tuple[float, float, float]


def satrec_from_tle(line1: str, line2: str) -> Satrec:
    """Parse a two-line element set into an SGP4 `Satrec`."""
    return Satrec.twoline2rv(line1, line2)


def epoch_datetime(satrec: Satrec) -> datetime:
    """Return the UTC datetime of a `Satrec`'s TLE epoch."""
    jd_epoch = satrec.jdsatepoch + satrec.jdsatepochF
    return _J2000_EPOCH + timedelta(days=jd_epoch - _J2000_JD)


def position_at(satrec: Satrec, at: datetime) -> Position:
    """Propagate `satrec` to the UTC instant `at` and return its position.

    Raises `PropagationError` if SGP4 cannot propagate to the requested time
    (e.g. the orbit has decayed).
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    else:
        at = at.astimezone(UTC)

    jd, fr = jday(at.year, at.month, at.day, at.hour, at.minute, at.second + at.microsecond / 1e6)
    error, teme_km, _teme_velocity = satrec.sgp4(jd, fr)
    if error != 0:
        raise PropagationError(_SGP4_ERRORS.get(error, f"SGP4 error code {error}"))

    theta = _gmst_radians(jd, fr)
    ecef_km = _teme_to_ecef(teme_km, theta)
    lat_deg, lon_deg, alt_km = _ecef_to_geodetic(ecef_km)
    return Position(
        lat_deg=lat_deg, lon_deg=lon_deg, alt_km=alt_km, teme_km=teme_km, ecef_km=ecef_km
    )


def _gmst_radians(jd: float, fr: float) -> float:
    """Greenwich Mean Sidereal Time (IAU-82) in radians for a Julian date."""
    t_ut1 = (jd + fr - _J2000_JD) / 36525.0
    seconds = (
        67310.54841
        + (876600.0 * 3600 + 8640184.812866) * t_ut1
        + 0.093104 * t_ut1**2
        - 6.2e-6 * t_ut1**3
    )
    degrees = (seconds / 240.0) % 360.0
    return math.radians(degrees)


def _teme_to_ecef(teme_km: tuple[float, float, float], theta: float) -> tuple[float, float, float]:
    """Rotate a TEME position vector into ECEF by the GMST angle `theta`."""
    x, y, z = teme_km
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return (x * cos_t + y * sin_t, -x * sin_t + y * cos_t, z)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> tuple[float, float, float]:
    """Convert WGS84 geodetic lat/lon (deg) and altitude (km) to ECEF (km).

    Inverse of `_ecef_to_geodetic`, used to place a ground observer (given as
    lat/lon/alt rather than a propagated TLE) into the same ECEF frame as a
    satellite for topocentric look-angle computation.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = _WGS84_A_KM / math.sqrt(1 - _WGS84_E2 * sin_lat**2)
    x = (n + alt_km) * cos_lat * math.cos(lon)
    y = (n + alt_km) * cos_lat * math.sin(lon)
    z = (n * (1 - _WGS84_E2) + alt_km) * sin_lat
    return x, y, z


def _ecef_to_geodetic(ecef_km: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert an ECEF position (km) to WGS84 geodetic lat/lon (deg) and alt (km)."""
    x, y, z = ecef_km
    lon = math.atan2(y, x)
    p = math.hypot(x, y)

    lat = math.atan2(z, p * (1 - _WGS84_E2))
    alt = 0.0
    for _ in range(5):
        sin_lat = math.sin(lat)
        n = _WGS84_A_KM / math.sqrt(1 - _WGS84_E2 * sin_lat**2)
        alt = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - _WGS84_E2 * n / (n + alt)))

    return math.degrees(lat), math.degrees(lon), alt
