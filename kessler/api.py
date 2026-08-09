"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query

from kessler.db import DEFAULT_DB_PATH, get_connection, get_satellite
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle

app = FastAPI(title="kessler", description="Satellite conjunction screening API")

STALE_THRESHOLD_HOURS = 72.0


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
        when = datetime.now(timezone.utc)
    elif at.tzinfo is None:
        when = at.replace(tzinfo=timezone.utc)
    else:
        when = at.astimezone(timezone.utc)

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
