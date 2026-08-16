"""FastAPI application exposing the kessler conjunction screening API."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kessler.catalog_cache import get_cached_catalog
from kessler.db import (
    DEFAULT_DB_PATH,
    SatelliteRecord,
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
from kessler.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
DEMO_HTML_PATH = STATIC_DIR / "demo.html"
WORLD_JSON_PATH = STATIC_DIR / "world.json"
SKY_HTML_PATH = STATIC_DIR / "sky.html"
CONJUNCTIONS_HTML_PATH = STATIC_DIR / "conjunctions.html"
ACCURACY_MD_PATH = Path(__file__).parent.parent / "docs" / "accuracy.md"

AUTO_INGEST_ENV_VAR = "KESSLER_AUTO_INGEST"
INGEST_REFRESH_INTERVAL_HOURS = 12.0


@dataclass(frozen=True)
class _HealthSnapshot:
    """Catalog size and newest epoch, as of the last refresh."""

    catalog_size: int
    newest_epoch: datetime | None


# /health must stay cheap even while a heavy /overhead or /conjunctions
# screen is running, since Fly's health check is what decides whether the
# machine is considered up. Reading catalog_size/newest_epoch straight from
# SQLite on every call previously meant every health check could contend
# with whatever ingest or request work was touching the same database file.
# Refreshed on every successful ingest (the only time these values actually
# change) and lazily on first use otherwise (e.g. a restart against an
# already-populated volume, where startup ingest is skipped entirely) --
# never recomputed on a schedule, so a warm process never touches the DB for
# this again.
_health_snapshot: _HealthSnapshot | None = None


def _compute_health_snapshot(conn: sqlite3.Connection) -> _HealthSnapshot:
    return _HealthSnapshot(catalog_size=count_satellites(conn), newest_epoch=latest_epoch(conn))


def _refresh_health_snapshot(db_path: str) -> None:
    """Recompute the health snapshot from `db_path` and cache it."""
    global _health_snapshot
    conn = get_connection(db_path)
    try:
        _health_snapshot = _compute_health_snapshot(conn)
    finally:
        conn.close()


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
    await asyncio.to_thread(_refresh_health_snapshot, db_path)


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

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

STALE_THRESHOLD_HOURS = 72.0
API_KEYS_ENV_VAR = "KESSLER_API_KEYS"

CONJUNCTION_DISCLAIMER = (
    "Geometric screening on public TLEs (SGP4), not a collision probability. "
    "No covariance is used; treat results as a geometric proximity estimate only."
)

# Screening is CPU-bound and, against a large or heavily-overlapping
# catalog, can take seconds to minutes -- see kessler/screen.py's time
# budget. Run it in a small, dedicated worker pool (not the default asyncio
# executor, which is much larger) so a burst of /conjunctions requests can
# never spin up enough concurrent screens to exhaust memory on a
# shared-cpu-1x/1GB machine, and never on the event loop thread itself,
# since that's what made a single heavy request look like the whole machine
# had stopped responding (health checks share that same thread).
SCREENING_MAX_WORKERS = int(os.environ.get("KESSLER_SCREENING_MAX_WORKERS", "2"))
_screening_executor = ThreadPoolExecutor(
    max_workers=SCREENING_MAX_WORKERS, thread_name_prefix="kessler-screen"
)

# Hard wall-clock cap per screen (kessler.screen.screen_catalog stops and
# reports `truncated: true` instead of hanging once this is exhausted).
# Screening no longer runs on the event loop (see _screening_executor
# above), so this budget only needs to keep worst-case *request* latency and
# worker-thread occupancy reasonable -- it no longer risks looking like a
# stuck machine to Fly's health check the way it did before that fix.
#
# 10s was too tight in production: a routine ISS-class target was hitting
# it and returning truncated results. Investigated against a real ~16k
# object Celestrak snapshot (`active` group) -- ISS (25544) has 467 catalog
# objects sharing its altitude band at the default threshold_km=10 (LEO
# near ISS's ~415km band is genuinely crowded; that's not a filter bug), and
# a full, untruncated screen of all 467 over the default 72h window took
# ~15.8s on ordinary dev hardware. 30s covers that with ~2x margin for a
# slower shared vCPU in production, while still bounding the pathological
# end (max threshold_km=50 pulls in ~11000 candidates and would take
# several minutes unconstrained -- that's exactly the case this budget
# exists to cut off, and it still will).
SCREENING_TIME_BUDGET_SECONDS = float(os.environ.get("KESSLER_SCREENING_TIME_BUDGET_SECONDS", "30"))

# Repeated or concurrent requests for the same target/window are common (the
# demo and sky-view pages, or several users watching the same object) and
# screening is expensive enough that serving them from cache instead of
# re-screening matters. Bounded in both time (a few minutes -- long enough
# to absorb a burst, short enough that results stay fresh against a catalog
# that re-ingests every 12h) and size (so cache memory can't grow with
# request variety).
SCREENING_CACHE_TTL_SECONDS = float(os.environ.get("KESSLER_SCREENING_CACHE_TTL_SECONDS", "180"))
SCREENING_CACHE_MAX_ENTRIES = 256
_screening_cache: TTLCache[tuple[int, int, float, float], dict[str, object]] = TTLCache(
    ttl_seconds=SCREENING_CACHE_TTL_SECONDS, max_entries=SCREENING_CACHE_MAX_ENTRIES
)

# /overhead propagates the *entire* catalog on every call (unlike
# /conjunctions, nothing about a satellite's current position can be pruned
# ahead of time), which is the same single-shared-vCPU-saturating shape of
# problem as screening -- it just got there via O(catalog) single
# propagations instead of O(candidates) full time-grid scans. Same fix, same
# worker pool (no separate pool: overhead and screening compete for the same
# bounded CPU budget either way, so one pool caps total concurrent work
# instead of letting two pools double it).
OVERHEAD_TIME_BUDGET_SECONDS = float(os.environ.get("KESSLER_OVERHEAD_TIME_BUDGET_SECONDS", "5"))

# The sky view polls every 30s from a location that doesn't change between
# polls (mod GPS/network-location jitter), and multiple callers are often in
# roughly the same place -- round lat/lon into the cache key so both share a
# cache entry instead of missing on noise. `alt_m` is deliberately not part
# of the key: realistic observer altitudes (0-a few km) change a satellite's
# look angles by a negligible fraction of a degree, so a cache hit computed
# for a slightly different altitude is still an accurate answer.
OVERHEAD_CACHE_TTL_SECONDS = float(os.environ.get("KESSLER_OVERHEAD_CACHE_TTL_SECONDS", "30"))
OVERHEAD_CACHE_MAX_ENTRIES = 256
OVERHEAD_LOCATION_ROUNDING_DECIMALS = 2
_overhead_cache: TTLCache[tuple[float, float, float], dict[str, object]] = TTLCache(
    ttl_seconds=OVERHEAD_CACHE_TTL_SECONDS, max_entries=OVERHEAD_CACHE_MAX_ENTRIES
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

    Served from the cached `_health_snapshot` (refreshed on every ingest)
    rather than querying the catalog directly, so a health check never
    contends with whatever ingest or request work is touching the database
    -- see the note above `_health_snapshot`. Falls back to a direct query
    only when nothing has warmed the cache yet (e.g. right after a restart
    against an already-populated volume, where startup ingest is skipped).
    """
    global _health_snapshot
    snapshot = _health_snapshot
    if snapshot is None:
        snapshot = _compute_health_snapshot(conn)
        _health_snapshot = snapshot

    newest_epoch_age_hours = (
        round((datetime.now(UTC) - snapshot.newest_epoch).total_seconds() / 3600, 3)
        if snapshot.newest_epoch is not None
        else None
    )
    return {
        "status": "ok",
        "catalog_size": snapshot.catalog_size,
        "newest_tle_epoch_utc": (
            snapshot.newest_epoch.isoformat() if snapshot.newest_epoch is not None else None
        ),
        "newest_tle_epoch_age_hours": newest_epoch_age_hours,
    }


@app.get(
    "/",
    tags=["demo"],
    summary="Landing page",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_index() -> HTMLResponse:
    """Serve the landing page: what kessler is, three live catalog numbers,
    and links into the sky view, world map, and API docs.

    Self-contained HTML/CSS/JS, no build step -- the live numbers are
    fetched client-side from `/health` and `/overhead`, same pattern as
    `/demo` and `/sky`.
    """
    return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))


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


def _render_inline_markdown(text: str) -> str:
    """Escape HTML and render `**bold**` and `` `code` `` spans."""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def _render_markdown(text: str) -> str:
    """Render a small subset of Markdown to HTML: headers, bold, inline
    code, paragraphs, and unordered lists.

    This is just enough to render `docs/accuracy.md`, not a general-purpose
    Markdown parser -- kessler has one Markdown doc to serve, so a small
    hand-rolled renderer is simpler than adding a dependency for it.
    """
    blocks = re.split(r"\n\s*\n", text.strip())
    rendered = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].startswith("### "):
            rendered.append(f"<h3>{_render_inline_markdown(lines[0][4:])}</h3>")
        elif lines[0].startswith("## "):
            rendered.append(f"<h2>{_render_inline_markdown(lines[0][3:])}</h2>")
        elif lines[0].startswith("# "):
            rendered.append(f"<h1>{_render_inline_markdown(lines[0][2:])}</h1>")
        elif lines[0].startswith("- "):
            items: list[str] = []
            for line in lines:
                if line.startswith("- "):
                    items.append(line[2:])
                else:
                    items[-1] += " " + line
            list_items = "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in items)
            rendered.append(f"<ul>{list_items}</ul>")
        else:
            rendered.append(f"<p>{_render_inline_markdown(' '.join(lines))}</p>")
    return "\n".join(rendered)


@app.get(
    "/docs/accuracy",
    tags=["demo"],
    summary="Accuracy notes (rendered docs/accuracy.md)",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_accuracy_docs() -> HTMLResponse:
    """Serve `docs/accuracy.md`, rendered to plain HTML, so the disclaimer
    linked from every page's footer resolves to a readable page rather than
    a raw repo file.
    """
    body = _render_markdown(ACCURACY_MD_PATH.read_text(encoding="utf-8"))
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kessler &mdash; accuracy notes</title>
<link rel="stylesheet" href="/static/shared.css">
<style>
  * {{ box-sizing: border-box; }}

  html, body {{
    margin: 0;
    padding: 0;
    min-height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: ui-monospace, "SF Mono", "Cascadia Mono", Consolas,
      "Liberation Mono", Menlo, monospace;
  }}

  main {{
    max-width: 720px;
    margin: 0 auto;
    padding: 2rem 1.25rem 3rem;
    line-height: 1.65;
    font-size: 0.9rem;
  }}

  main h1 {{ font-size: 1.4rem; }}
  main h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  main code {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 0.1rem 0.3rem;
    font-size: 0.85em;
  }}
  main a {{ color: var(--accent); }}
  main ul {{ padding-left: 1.25rem; }}
  main li {{ margin-bottom: 0.5rem; }}
</style>
</head>
<body>
  <header class="kessler-header">
    <a class="wordmark" href="/">kessler</a>
    <nav class="kessler-nav">
      <a href="/sky">Sky view</a>
      <a href="/demo">World map</a>
      <a href="/docs">API</a>
    </nav>
  </header>

  <main>
{body}
  </main>

  <footer class="kessler-footer">
    <span>Data: <a href="https://celestrak.org/" target="_blank" rel="noopener">Celestrak</a></span>
    <span class="sep">&middot;</span>
    <span>Geometric miss distance only, not a collision probability &mdash;
      <a href="/docs/accuracy">accuracy notes</a></span>
    <span class="sep">&middot;</span>
    <a href="/docs">API docs</a>
  </footer>
</body>
</html>
"""
    return HTMLResponse(page)


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


DEFAULT_POSITIONS_SAMPLE_SIZE = 300
MAX_POSITIONS_SAMPLE_SIZE = 1000


@app.get(
    "/satellites/positions",
    tags=["satellites"],
    summary="Current positions for a live sample of the catalog",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "at": "2026-08-16T12:00:00+00:00",
                        "count": 2,
                        "satellites": [
                            {
                                "norad_id": 25544,
                                "name": "ISS (ZARYA)",
                                "lat": 12.345,
                                "lon": -45.679,
                                "alt_km": 420.123,
                                "epoch_age_hours": 5.1,
                                "stale": False,
                            },
                            {
                                "norad_id": 43013,
                                "name": "NOAA 20",
                                "lat": -32.1,
                                "lon": 88.4,
                                "alt_km": 824.6,
                                "epoch_age_hours": 11.4,
                                "stale": False,
                            },
                        ],
                    }
                }
            }
        },
        422: {"description": "`limit` outside its allowed range"},
    },
)
async def get_positions_sample(
    limit: int = Query(
        default=DEFAULT_POSITIONS_SAMPLE_SIZE,
        ge=1,
        le=MAX_POSITIONS_SAMPLE_SIZE,
        description="Maximum number of satellites to return.",
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Return current geodetic positions for a spread sample of the catalog.

    Unlike `/satellites/{norad_id}/position` (one object, one request), this
    returns many at once -- e.g. for plotting a live sample of the catalog
    on a map -- without a round trip per object. The sample is a stride
    across the full, catalog-order-independent catalog (every Nth record,
    not just the first `limit`), so it spans the whole catalog rather than
    clustering however the DB happens to order rows. Records whose TLE
    fails to propagate are silently skipped, consistent with `/overhead`.

    Reuses the same per-object `Satrec` cache as `/overhead` and
    `/conjunctions` (see `kessler/catalog_cache.py`); a few hundred
    propagations is cheap enough to run directly on the event loop, unlike
    those two.
    """
    at = datetime.now(UTC)
    catalog = list_satellites(conn)
    catalog_cache = get_cached_catalog(catalog)

    stride = max(1, len(catalog) // limit) if catalog else 1
    sample = catalog[::stride][:limit]

    satellites = []
    for record in sample:
        cached = catalog_cache.get(record.norad_id)
        satrec = (
            cached.satrec if cached is not None else satrec_from_tle(record.line1, record.line2)
        )
        try:
            position = position_at(satrec, at)
        except PropagationError:
            continue

        epoch_age_hours = (at - epoch_datetime(satrec)).total_seconds() / 3600
        satellites.append(
            {
                "norad_id": record.norad_id,
                "name": record.name,
                "lat": round(position.lat_deg, 3),
                "lon": round(position.lon_deg, 3),
                "alt_km": round(position.alt_km, 3),
                "epoch_age_hours": round(epoch_age_hours, 3),
                "stale": epoch_age_hours > STALE_THRESHOLD_HOURS,
            }
        )

    return {
        "at": at.isoformat(),
        "count": len(satellites),
        "satellites": satellites,
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
                        "truncated": False,
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

    The actual screen runs off the event loop in a worker thread, under a
    hard time budget (`truncated: true` in the response if it was hit
    before the full catalog could be checked), and results are cached for a
    few minutes per (norad_id, hours, threshold_km, min_separation_km) so
    repeated or concurrent requests for the same target are served without
    re-screening. See `kessler/screen.py` and `kessler/catalog_cache.py`.
    """
    target = get_satellite(conn, norad_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown norad_id: {norad_id}")

    cache_key = (norad_id, hours, threshold_km, min_separation_km)
    cached = _screening_cache.get(cache_key)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(
        _screening_executor,
        _screen_target,
        conn,
        target,
        hours,
        threshold_km,
        min_separation_km,
    )

    _screening_cache.set(cache_key, payload)
    return payload


@app.get(
    "/conjunctions/{norad_id}/view",
    tags=["conjunctions"],
    summary="Human-readable conjunctions page",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_conjunctions_view(norad_id: int) -> HTMLResponse:
    """Serve a human-readable page for a target's conjunctions.

    `GET /conjunctions/{norad_id}` on its own is raw JSON, which looks
    broken to anyone who lands on it directly (e.g. via the sky view's
    "see conjunctions" link). This is a self-contained HTML/CSS/JS shell,
    same pattern as `/demo` and `/sky` -- `norad_id` isn't server-side
    templated in at all; the page reads it back out of its own URL and
    calls the JSON endpoint above from the browser. The `norad_id: int`
    parameter here exists only so FastAPI 422s a non-numeric ID immediately
    rather than serving the shell for it; an unknown-but-numeric ID instead
    surfaces the JSON endpoint's own 404 client-side.
    """
    return HTMLResponse(CONJUNCTIONS_HTML_PATH.read_text(encoding="utf-8"))


def _screen_target(
    conn: sqlite3.Connection,
    target: SatelliteRecord,
    hours: int,
    threshold_km: float,
    min_separation_km: float,
) -> dict[str, object]:
    """Blocking worker: read the catalog, screen it, and build the response.

    Runs on a worker thread (see `get_conjunctions`) so a heavy screen never
    runs on the event loop -- which is what previously made a single slow
    `/conjunctions` request block health checks and every other in-flight
    request, making the whole machine look unresponsive.
    """
    window_start = datetime.now(UTC)
    window_end = window_start + timedelta(hours=hours)

    catalog = list_satellites(conn)
    catalog_cache = get_cached_catalog(catalog)

    results, truncated = screen_catalog(
        target,
        catalog,
        window_start,
        window_end,
        threshold_km,
        min_separation_km,
        catalog_cache=catalog_cache,
        time_budget_seconds=SCREENING_TIME_BUDGET_SECONDS,
    )

    return {
        "disclaimer": CONJUNCTION_DISCLAIMER,
        "target_norad_id": target.norad_id,
        "target_name": target.name,
        "window_start_utc": window_start.isoformat(),
        "window_end_utc": window_end.isoformat(),
        "threshold_km": threshold_km,
        "min_separation_km": min_separation_km,
        "truncated": truncated,
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
                        "truncated": False,
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

    Like `/conjunctions`, the actual computation runs off the event loop in
    a worker thread, under a hard time budget (`truncated: true` in the
    response if it was hit before the full catalog could be checked), and
    is cached for 30s per (rounded lat, rounded lon, min_elevation_deg) so
    repeated or concurrent requests for the same location don't
    re-propagate the whole catalog. See `kessler/overhead.py` and
    `kessler/catalog_cache.py`.
    """
    cache_key = (
        round(lat, OVERHEAD_LOCATION_ROUNDING_DECIMALS),
        round(lon, OVERHEAD_LOCATION_ROUNDING_DECIMALS),
        min_elevation_deg,
    )
    cached = _overhead_cache.get(cache_key)
    if cached is None:
        loop = asyncio.get_running_loop()
        cached = await loop.run_in_executor(
            _screening_executor,
            _compute_overhead,
            conn,
            lat,
            lon,
            alt_m / 1000.0,
            min_elevation_deg,
        )
        _overhead_cache.set(cache_key, cached)

    return {
        "at": cached["at"],
        "observer": {"lat": lat, "lon": lon, "alt_m": alt_m},
        "min_elevation_deg": cached["min_elevation_deg"],
        "count": cached["count"],
        "truncated": cached["truncated"],
        "satellites": cached["satellites"],
    }


def _compute_overhead(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    alt_km: float,
    min_elevation_deg: float,
) -> dict[str, object]:
    """Blocking worker: propagate the catalog and find overhead satellites.

    Runs on a worker thread (see `get_overhead`), same as `_screen_target`,
    so a large or slow-to-propagate catalog never runs on the event loop --
    that's what previously let a single /overhead request saturate the
    machine's one shared vCPU and starve health checks for long enough that
    Fly marked it unhealthy.

    Returns only the computed fields, not `observer` -- that's echoed back
    from the actual request's `lat`/`lon`/`alt_m` in `get_overhead`, not
    from whichever request happened to populate this cache entry.
    """
    at = datetime.now(UTC)
    catalog = list_satellites(conn)
    catalog_cache = get_cached_catalog(catalog)

    satellites, truncated = find_overhead(
        catalog,
        lat,
        lon,
        alt_km,
        at,
        min_elevation_deg,
        STALE_THRESHOLD_HOURS,
        catalog_cache=catalog_cache,
        time_budget_seconds=OVERHEAD_TIME_BUDGET_SECONDS,
    )

    return {
        "at": at.isoformat(),
        "min_elevation_deg": min_elevation_deg,
        "count": len(satellites),
        "truncated": truncated,
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
