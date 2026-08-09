"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query

from kessler.db import DEFAULT_DB_PATH, get_connection, get_satellite, list_satellites
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle
from kessler.screen import screen_catalog

app = FastAPI(title="kessler", description="Satellite conjunction screening API")

STALE_THRESHOLD_HOURS = 72.0

CONJUNCTION_DISCLAIMER = (
    "Geometric screening on public TLEs (SGP4), not a collision probability. "
    "No covariance is used; treat results as a geometric proximity estimate only."
)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency yielding a SQLite connection to the satellite catalog."""
    db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}


@app.get("/satellites/{norad_id}/position")
async def get_position(
    norad_id: int,
    at: datetime | None = Query(
        default=None, description="UTC timestamp (ISO 8601). Defaults to now."
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Return a satellite's geodetic position at a given instant via SGP4."""
    satellite = get_satellite(conn, norad_id)
    if satellite is None:
        raise HTTPException(status_code=404, detail=f"Unknown norad_id: {norad_id}")

    if at is None:
        when = datetime.now(UTC)
    elif at.tzinfo is None:
        when = at.replace(tzinfo=UTC)
    else:
        when = at.astimezone(UTC)

    satrec = satrec_from_tle(satellite.line1, satellite.line2)
    epoch = epoch_datetime(satrec)

    try:
        position = position_at(satrec, when)
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    epoch_age_hours = (when - epoch).total_seconds() / 3600

    return {
        "norad_id": satellite.norad_id,
        "name": satellite.name,
        "at": when.isoformat(),
        "lat": round(position.lat_deg, 6),
        "lon": round(position.lon_deg, 6),
        "alt_km": round(position.alt_km, 3),
        "epoch_utc": epoch.isoformat(),
        "epoch_age_hours": round(epoch_age_hours, 3),
        "stale": epoch_age_hours > STALE_THRESHOLD_HOURS,
    }


@app.get("/conjunctions/{norad_id}")
async def get_conjunctions(
    norad_id: int,
    hours: int = Query(default=72, ge=1, le=168, description="Screening window length in hours."),
    threshold_km: float = Query(
        default=10.0,
        ge=1,
        le=50,
        description="Coarse-filter buffer and candidate miss-distance bound, in km.",
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Screen a target satellite against the catalog for conjunctions.

    Reports geometric miss distance only, from public TLEs via SGP4. This is
    not a collision probability and does not account for TLE covariance.
    """
    target = get_satellite(conn, norad_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown norad_id: {norad_id}")

    window_start = datetime.now(UTC)
    window_end = window_start + timedelta(hours=hours)

    catalog = list_satellites(conn)
    results = screen_catalog(target, catalog, window_start, window_end, threshold_km)

    return {
        "disclaimer": CONJUNCTION_DISCLAIMER,
        "target_norad_id": target.norad_id,
        "target_name": target.name,
        "window_start_utc": window_start.isoformat(),
        "window_end_utc": window_end.isoformat(),
        "threshold_km": threshold_km,
        "conjunctions": [
            {
                "other_norad_id": r.other_norad_id,
                "other_name": r.other_name,
                "tca_utc": r.tca.isoformat(),
                "miss_distance_km": round(r.miss_distance_km, 3),
                "target_epoch_age_hours": round(r.target_epoch_age_hours, 3),
                "other_epoch_age_hours": round(r.other_epoch_age_hours, 3),
            }
            for r in results
        ],
    }
