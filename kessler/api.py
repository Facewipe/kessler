"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from starlette.responses import JSONResponse, Response

from kessler.db import DEFAULT_DB_PATH, get_connection, get_satellite
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle

API_KEYS_ENV_VAR = "KESSLER_API_KEYS"
API_KEY_HEADER = "X-API-Key"
STALE_THRESHOLD_HOURS = 72.0

app = FastAPI(
    title="kessler",
    description=(
        "Satellite conjunction screening API built on open orbital data "
        "(Celestrak GP/TLE data for the MVP).\n\n"
        "**Accuracy note:** all results are derived from SGP4 propagation of "
        "publicly available TLEs. Accuracy is roughly km-level near a TLE's "
        "epoch and degrades as the TLE ages — see `epoch_age_hours` and "
        "`stale` in responses, and the `docs/accuracy.md` file in this "
        "repository for details. The API reports **geometric miss distance "
        "only**; it does not compute or report collision probability."
    ),
    version="0.1.0",
)


def _configured_api_keys() -> set[str] | None:
    """Return the configured API keys, or None if `KESSLER_API_KEYS` is unset.

    Reading the environment on every call (rather than caching at import
    time) keeps this simple and lets tests toggle the env var freely.
    """
    raw = os.environ.get(API_KEYS_ENV_VAR, "")
    keys = {key.strip() for key in raw.split(",") if key.strip()}
    return keys or None


@app.middleware("http")
async def api_key_auth(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Require a valid `X-API-Key` header when `KESSLER_API_KEYS` is set.

    `/health` is always open so uptime checks don't need a key. When
    `KESSLER_API_KEYS` is unset, the API stays fully open (dev mode).
    """
    if request.url.path == "/health":
        return await call_next(request)

    api_keys = _configured_api_keys()
    if api_keys is not None and request.headers.get(API_KEY_HEADER) not in api_keys:
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid API key"})

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
    """Return service health status. Always open, even when an API key is configured."""
    return {"status": "ok"}


@app.get(
    "/satellites/{norad_id}/position",
    tags=["satellites"],
    summary="Get a satellite's geodetic position at a given instant",
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
        404: {
            "description": "Unknown `norad_id`",
            "content": {
                "application/json": {"example": {"detail": "Unknown norad_id: 999999"}}
            },
        },
        422: {"description": "Invalid `at` timestamp or a non-propagable TLE"},
    },
)
async def get_position(
    norad_id: int,
    at: datetime | None = Query(
        default=None,
        description="UTC timestamp (ISO 8601). Defaults to now.",
        examples=["2026-08-09T12:00:00Z"],
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Propagate a satellite's most recently ingested TLE via SGP4 and return its
    geodetic position (lat/lon/alt) at the given instant, along with the TLE's
    epoch age and staleness flag. Reports geometric position only — no
    covariance or collision probability.
    """
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
