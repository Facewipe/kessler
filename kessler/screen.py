"""Conjunction screening: coarse filtering and TCA/miss-distance refinement.

We report geometric miss distance only, derived from public TLEs via SGP4.
There is no covariance and no collision probability -- see CLAUDE.md's domain
notes. Screening proceeds in three stages:

1. Coarse filter: drop catalog objects whose (perigee - buffer, apogee +
   buffer) altitude range does not overlap the target's.
2. Coarse propagation of remaining pairs over the window at
   `COARSE_STEP_SECONDS` steps, collecting local minima of separation below
   a candidate bound.
3. Refinement around each candidate at `FINE_STEP_SECONDS` steps to find TCA
   and miss distance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sgp4.api import Satrec

from kessler.db import SatelliteRecord
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle

_EARTH_RADIUS_KM = 6378.137
_EARTH_MU_KM3_S2 = 398600.4418

COARSE_STEP_SECONDS = 60.0
FINE_STEP_SECONDS = 1.0


@dataclass(frozen=True)
class OrbitRange:
    """A satellite's approximate perigee/apogee altitude (km) at TLE epoch."""

    perigee_km: float
    apogee_km: float


@dataclass(frozen=True)
class ConjunctionCandidate:
    """A close-approach event found between two objects."""

    tca: datetime
    miss_distance_km: float


@dataclass(frozen=True)
class ConjunctionResult:
    """The closest approach between the target and one other catalog object."""

    other_norad_id: int
    other_name: str
    tca: datetime
    miss_distance_km: float
    target_epoch_age_hours: float
    other_epoch_age_hours: float


def orbit_range(satrec: Satrec) -> OrbitRange:
    """Approximate perigee/apogee altitude (km) from a `Satrec`'s mean elements.

    Uses Kepler's third law on the TLE's mean motion (`no_kozai`) to derive a
    semi-major axis, then the TLE eccentricity for perigee/apogee. This is a
    coarse approximation (mean, not osculating, elements) intended only to
    prune obviously non-overlapping orbits before propagation.
    """
    mean_motion_rad_s = satrec.no_kozai / 60.0
    semi_major_axis_km = (_EARTH_MU_KM3_S2 / mean_motion_rad_s**2) ** (1 / 3)
    perigee_km = semi_major_axis_km * (1 - satrec.ecco) - _EARTH_RADIUS_KM
    apogee_km = semi_major_axis_km * (1 + satrec.ecco) - _EARTH_RADIUS_KM
    return OrbitRange(perigee_km=perigee_km, apogee_km=apogee_km)


def ranges_overlap(a: OrbitRange, b: OrbitRange, buffer_km: float) -> bool:
    """True if `a`'s altitude range, expanded by `buffer_km`, overlaps `b`'s."""
    return (a.perigee_km - buffer_km) <= (b.apogee_km + buffer_km) and (
        b.perigee_km - buffer_km
    ) <= (a.apogee_km + buffer_km)


def find_close_approaches(
    target: Satrec,
    other: Satrec,
    start: datetime,
    end: datetime,
    candidate_bound_km: float,
    coarse_step_seconds: float = COARSE_STEP_SECONDS,
    fine_step_seconds: float = FINE_STEP_SECONDS,
) -> list[ConjunctionCandidate]:
    """Find local-minimum close approaches between two satellites over [start, end].

    Coarsely samples the window at `coarse_step_seconds`, collects local minima
    of separation distance at or below `candidate_bound_km`, then refines each
    with a fine linear search (step `fine_step_seconds`) over the surrounding
    coarse interval to locate TCA and miss distance.
    """
    coarse_times = _time_grid(start, end, coarse_step_seconds)
    if len(coarse_times) < 2:
        return []

    distances = [_distance_km(target, other, t) for t in coarse_times]

    candidates: list[ConjunctionCandidate] = []
    last_index = len(distances) - 1
    for i, dist in enumerate(distances):
        if dist > candidate_bound_km:
            continue
        is_local_min = (i == 0 or distances[i - 1] >= dist) and (
            i == last_index or distances[i + 1] >= dist
        )
        if not is_local_min:
            continue

        window_start = coarse_times[max(i - 1, 0)]
        window_end = coarse_times[min(i + 1, last_index)]
        candidates.append(_refine(target, other, window_start, window_end, fine_step_seconds))

    return candidates


def screen_catalog(
    target: SatelliteRecord,
    catalog: Iterable[SatelliteRecord],
    start: datetime,
    end: datetime,
    threshold_km: float,
) -> list[ConjunctionResult]:
    """Screen `target` against `catalog` for conjunctions over [start, end].

    Applies a coarse apogee/perigee overlap filter (buffered by
    `threshold_km`) to prune the catalog, then searches surviving pairs for
    close approaches at or below `threshold_km`. Epoch ages are measured
    relative to `start`. Results are sorted by miss distance, ascending.
    """
    target_satrec = satrec_from_tle(target.line1, target.line2)
    target_range = orbit_range(target_satrec)
    target_epoch = epoch_datetime(target_satrec)
    target_epoch_age_hours = (start - target_epoch).total_seconds() / 3600

    results: list[ConjunctionResult] = []
    for other in catalog:
        if other.norad_id == target.norad_id:
            continue

        other_satrec = satrec_from_tle(other.line1, other.line2)
        if not ranges_overlap(target_range, orbit_range(other_satrec), threshold_km):
            continue

        try:
            candidates = find_close_approaches(
                target_satrec, other_satrec, start, end, threshold_km
            )
        except PropagationError:
            continue
        if not candidates:
            continue

        closest = min(candidates, key=lambda c: c.miss_distance_km)
        other_epoch_age_hours = (start - epoch_datetime(other_satrec)).total_seconds() / 3600
        results.append(
            ConjunctionResult(
                other_norad_id=other.norad_id,
                other_name=other.name,
                tca=closest.tca,
                miss_distance_km=closest.miss_distance_km,
                target_epoch_age_hours=target_epoch_age_hours,
                other_epoch_age_hours=other_epoch_age_hours,
            )
        )

    results.sort(key=lambda r: r.miss_distance_km)
    return results


def _distance_km(satrec_a: Satrec, satrec_b: Satrec, at: datetime) -> float:
    """Euclidean distance (km) between two satellites' TEME positions at `at`."""
    pos_a = position_at(satrec_a, at)
    pos_b = position_at(satrec_b, at)
    dx = pos_a.teme_km[0] - pos_b.teme_km[0]
    dy = pos_a.teme_km[1] - pos_b.teme_km[1]
    dz = pos_a.teme_km[2] - pos_b.teme_km[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _refine(
    target: Satrec,
    other: Satrec,
    window_start: datetime,
    window_end: datetime,
    fine_step_seconds: float,
) -> ConjunctionCandidate:
    """Fine-step linear search for TCA/miss distance within a coarse candidate window."""
    fine_times = _time_grid(window_start, window_end, fine_step_seconds)
    best_time = fine_times[0]
    best_distance = _distance_km(target, other, best_time)
    for t in fine_times[1:]:
        distance = _distance_km(target, other, t)
        if distance < best_distance:
            best_distance = distance
            best_time = t
    return ConjunctionCandidate(tca=best_time, miss_distance_km=best_distance)


def _time_grid(start: datetime, end: datetime, step_seconds: float) -> list[datetime]:
    """Inclusive list of datetimes from `start` to `end` at `step_seconds` spacing."""
    if step_seconds <= 0:
        raise ValueError("step_seconds must be positive")
    total_seconds = (end - start).total_seconds()
    if total_seconds < 0:
        raise ValueError("end must not be before start")

    steps = math.floor(total_seconds / step_seconds)
    times = [start + timedelta(seconds=i * step_seconds) for i in range(steps + 1)]
    if times[-1] < end:
        times.append(end)
    return times
