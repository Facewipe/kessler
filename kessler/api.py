"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from kessler.db import DEFAULT_DB_PATH, get_connection, get_satellite, list_satellites
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle
from kessler.screen import DEFAULT_MIN_SEPARATION_KM, screen_catalog

app = FastAPI(
    title="kessler",
    description=(
        "Satellite conjunction screening API built on open orbital data "
        "(Celestrak GP/TLE data, propagated via SGP4).\n\n"
        "TLE-based propagation is roughly km-level accurate near a TLE's "
        "epoch and degrades as the TLE ages; every response reports "
        "`epoch_age_hours` and flags TLEs older than 72 hours as `stale`. "
        "Conjunction results report **geometric miss distance only** — "
        "this is not a collision probability. See `docs/accuracy.md` for "
        "the full explanation.\n\n"
        "Set `KESSLER_API_KEYS` (comma-separated) to require an `X-API-Key` "
        "header on every endpoint except `/health`; leave it unset for open "
        "(dev-mode) access."
    ),
    version="0.1.0",
)

STALE_THRESHOLD_HOURS = 72.0
API_KEYS_ENV_VAR = "KESSLER_API_KEYS"

CONJUNCTION_DISCLAIMER = (
    "Geometric screening on public TLEs (SGP4), not a collision probability. "
    "No covariance is used; treat results as a geometric proximity estimate only."
)


def _configured_api_keys() -> set[str]:
    """Return the configured API keys, or an empty set if auth is disabled."""
    raw = os.environ.get(API_KEYS_ENV_VAR, "")
    return {key.strip() for key in raw.split(",") if key.strip()}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Require a valid X-API-Key header when KESSLER_API_KEYS is set.

    `/health` always stays open so uptime checks don't need a key. When
    KESSLER_API_KEYS is unset, the whole API is open (dev mode).
    """
    api_keys = _configured_api_keys()
    if api_keys and request.url.path != "/health":
        if request.headers.get("X-API-Key") not in api_keys:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency yielding a SQLite connection to the satellite catalog."""
    db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


@app.get(
    "/health",
    tags=["health"],
    summary="Service health check",
    responses={200: {"content": {"application/json": {"example": {"status": "ok"}}}}},
)
async def health() -> dict[str, str]:
    """Return service health status. Always open, even when API keys are configured."""
    return {"status": "ok"}


@app.get(
    "/satellites/{norad_id}/position",
    tags=["satellites"],
    summary="Get a satellite's current geodetic position",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "norad_id": 25544,
                        "name": "ISS (ZARYA)",
                        "at": "2026-08-09T12:00:00+00:00",
                        "lat": 12.345678,
                        "lon": -45.678901,
                        "alt_km": 420.123,
                        "epoch_utc": "2026-08-08T03:15:22.123456+00:00",
                        "epoch_age_hours": 32.744,
                        "stale": False,
                    }
                }
            }
        },
        404: {"description": "Unknown norad_id"},
        422: {"description": "Invalid `at` timestamp"},
    },
)
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


@app.get(
    "/conjunctions/{norad_id}",
    tags=["conjunctions"],
    summary="Screen a satellite for conjunctions",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "disclaimer": CONJUNCTION_DISCLAIMER,
                        "target_norad_id": 25544,
                        "target_name": "ISS (ZARYA)",
                        "window_start_utc": "2026-08-09T12:00:00+00:00",
                        "window_end_utc": "2026-08-12T12:00:00+00:00",
                        "threshold_km": 10.0,
                        "min_separation_km": 1.0,
                        "conjunctions": [
                            {
                                "other_norad_id": 43205,
                                "other_name": "STARLINK-1007",
                                "tca_utc": "2026-08-10T03:12:47+00:00",
                                "miss_distance_km": 3.842,
                                "target_epoch_age_hours": 5.1,
                                "other_epoch_age_hours": 12.4,
                            }
                        ],
                    }
                }
            }
        },
        404: {"description": "Unknown norad_id"},
        422: {"description": "`hours` or `threshold_km` outside their allowed ranges"},
    },
)
async def get_conjunctions(
    norad_id: int,
    hours: int = Query(default=72, ge=1, le=168, description="Screening window length in hours."),
    threshold_km: float = Query(
        default=10.0,
        ge=1,
        le=50,
        description="Coarse-filter buffer and candidate miss-distance bound, in km.",
    ),
    min_separation_km: float = Query(
        default=DEFAULT_MIN_SEPARATION_KM,
        ge=0,
        le=50,
        description=(
            "Pairs whose separation never exceeds this value anywhere in the "
            "window are treated as co-located (e.g. docked spacecraft or a "
            "station's own modules) and excluded from results. This bound is "
            "widened automatically when the pair's TLEs have different epoch "
            "ages, since an older TLE naturally drifts further from a "
            "fresher one under propagation even for a physically co-located "
            "object."
        ),
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
    results = screen_catalog(
        target, catalog, window_start, window_end, threshold_km, min_separation_km
    )

    return {
        "disclaimer": CONJUNCTION_DISCLAIMER,
        "target_norad_id": target.norad_id,
        "target_name": target.name,
        "window_start_utc": window_start.isoformat(),
        "window_end_utc": window_end.isoformat(),
        "threshold_km": threshold_km,
        "min_separation_km": min_separation_km,
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
