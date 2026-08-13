"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from kessler.db import (
    DEFAULT_DB_PATH,
    count_satellites,
    get_connection,
    get_satellite,
    latest_epoch,
    list_satellites,
)
from kessler.ingest import run_ingest
from kessler.overhead import DEFAULT_MIN_ELEVATION_DEG, find_overhead
from kessler.propagate import PropagationError, epoch_datetime, position_at, satrec_from_tle
from kessler.screen import DEFAULT_MIN_SEPARATION_KM, screen_catalog

logger = logging.getLogger(__name__)

DEMO_HTML_PATH = Path(__file__).parent / "static" / "demo.html"
WORLD_JSON_PATH = Path(__file__).parent / "static" / "world.json"
SKY_HTML_PATH = Path(__file__).parent / "static" / "sky.html"

AUTO_INGEST_ENV_VAR = "KESSLER_AUTO_INGEST"
INGEST_REFRESH_INTERVAL_HOURS = 12.0


def _auto_ingest_enabled() -> bool:
    """Whether startup/periodic auto-ingest is enabled (on by default).

    Set `KESSLER_AUTO_INGEST=0` to disable, e.g. in tests, so the app never
    makes a network call on startup.
    """
    return os.environ.get(AUTO_INGEST_ENV_VAR, "1").strip().lower() not in {"0", "false", "no"}


async def _ingest_and_log(reason: str) -> None:
    """Run catalog ingestion off the event loop and log a one-line summary."""
    db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)
    try:
        summary = await asyncio.to_thread(run_ingest, db_path)
    except Exception:
        logger.exception("Catalog ingest (%s) failed", reason)
        return
    logger.info("Catalog ingest (%s): %s", reason, summary)


async def _startup_ingest_if_empty() -> None:
    """Run ingest once at startup if the catalog is empty (fresh deploy/volume)."""
    db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    try:
        empty = count_satellites(conn) == 0
    finally:
        conn.close()
    if empty:
        await _ingest_and_log("startup, empty catalog")


async def _periodic_ingest_refresh() -> None:
    """Re-run ingest every `INGEST_REFRESH_INTERVAL_HOURS` for the app's lifetime."""
    while True:
        await asyncio.sleep(INGEST_REFRESH_INTERVAL_HOURS * 3600)
        await _ingest_and_log("scheduled refresh")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start background catalog ingestion tasks for the app's lifetime."""
    background_tasks: list[asyncio.Task[None]] = []
    if _auto_ingest_enabled():
        background_tasks.append(asyncio.create_task(_startup_ingest_if_empty()))
        background_tasks.append(asyncio.create_task(_periodic_ingest_refresh()))
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


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
    lifespan=lifespan,
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
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "catalog_size": 8412,
                        "newest_tle_epoch_utc": "2026-08-13T09:12:00+00:00",
                        "newest_tle_epoch_age_hours": 3.1,
                    }
                }
            }
        }
    },
)
async def health(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    """Return service health plus catalog freshness. Always open, even when API keys are configured.

    `catalog_size` and `newest_tle_epoch_age_hours` make deployment problems
    (empty catalog, stalled ingestion) visible without digging into logs.
    """
    newest_epoch = latest_epoch(conn)
    newest_epoch_age_hours = (
        round((datetime.now(UTC) - newest_epoch).total_seconds() / 3600, 3)
        if newest_epoch is not None
        else None
    )
    return {
        "status": "ok",
        "catalog_size": count_satellites(conn),
        "newest_tle_epoch_utc": newest_epoch.isoformat() if newest_epoch is not None else None,
        "newest_tle_epoch_age_hours": newest_epoch_age_hours,
    }


@app.get(
    "/demo",
    tags=["demo"],
    summary="Live demo map of the API",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_demo() -> HTMLResponse:
    """Serve a self-contained HTML page plotting live positions for a curated
    set of well-known satellites on a 2D map.

    This is a shop window for the API, not a product UI: plain HTML/CSS/JS,
    no build step and no external dependencies. It calls the existing
    `/satellites/{norad_id}/position` and `/conjunctions/{norad_id}`
    endpoints from the browser.
    """
    return HTMLResponse(DEMO_HTML_PATH.read_text(encoding="utf-8"))


@app.get(
    "/world.json",
    tags=["demo"],
    summary="Simplified world land polygons for the demo map",
    responses={200: {"content": {"application/json": {}}}},
)
async def get_world_map() -> FileResponse:
    """Serve the pre-built land polygon data used by `/demo`'s map.

    Built ahead of time by `scripts/build_map.py` and committed to the
    repo, so the demo page renders offline with no runtime download from
    a third party.
    """
    return FileResponse(WORLD_JSON_PATH, media_type="application/json")


@app.get(
    "/sky",
    tags=["demo"],
    summary="Live polar sky chart of what's overhead right now",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_sky() -> HTMLResponse:
    """Serve a self-contained polar sky-chart page.

    Plots every satellite `/overhead` reports for the browser's geolocation
    (or a London fallback) as a dot on a horizon-to-zenith polar plot,
    refreshed every 30 seconds. Plain HTML/CSS/JS, no build step and no
    external dependencies -- a shop window for `/overhead`, not a product UI.
    """
    return HTMLResponse(SKY_HTML_PATH.read_text(encoding="utf-8"))


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


@app.get(
    "/overhead",
    tags=["satellites"],
    summary="List catalog satellites currently above an observer's horizon",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "at": "2026-08-12T12:00:00+00:00",
                        "observer": {"lat": 51.5074, "lon": -0.1278, "alt_m": 0.0},
                        "min_elevation_deg": 10.0,
                        "count": 1,
                        "satellites": [
                            {
                                "norad_id": 25544,
                                "name": "ISS (ZARYA)",
                                "elevation_deg": 45.213,
                                "azimuth_deg": 132.704,
                                "range_km": 850.331,
                                "alt_km": 420.123,
                                "epoch_age_hours": 5.1,
                                "stale": False,
                            }
                        ],
                    }
                }
            }
        },
        422: {"description": "`lat`, `lon`, or `min_elevation_deg` outside their allowed ranges"},
    },
)
async def get_overhead(
    lat: float = Query(..., ge=-90, le=90, description="Observer latitude, degrees."),
    lon: float = Query(..., ge=-180, le=180, description="Observer longitude, degrees."),
    min_elevation_deg: float = Query(
        default=DEFAULT_MIN_ELEVATION_DEG,
        ge=0,
        le=90,
        description="Minimum elevation above the horizon to report, degrees.",
    ),
    alt_m: float = Query(
        default=0.0, description="Observer altitude above the WGS84 ellipsoid, meters."
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Return every catalog satellite above the observer's horizon right now.

    Propagates the full catalog to the current time, prunes by ground-track
    distance from the observer before the full topocentric conversion (see
    `kessler.overhead`), and returns satellites at or above
    `min_elevation_deg`, sorted by elevation descending.
    """
    at = datetime.now(UTC)
    catalog = list_satellites(conn)
    satellites = find_overhead(
        catalog, lat, lon, alt_m / 1000.0, at, min_elevation_deg, STALE_THRESHOLD_HOURS
    )

    return {
        "at": at.isoformat(),
        "observer": {"lat": lat, "lon": lon, "alt_m": alt_m},
        "min_elevation_deg": min_elevation_deg,
        "count": len(satellites),
        "satellites": [
            {
                "norad_id": s.norad_id,
                "name": s.name,
                "elevation_deg": round(s.elevation_deg, 3),
                "azimuth_deg": round(s.azimuth_deg, 3),
                "range_km": round(s.range_km, 3),
                "alt_km": round(s.alt_km, 3),
                "epoch_age_hours": round(s.epoch_age_hours, 3),
                "stale": s.stale,
            }
            for s in satellites
        ],
    }
