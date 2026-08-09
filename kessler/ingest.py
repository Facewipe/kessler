"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from kessler.db import DEFAULT_DB_PATH, SatelliteRecord, get_connection, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_GROUP = "active"

# Celestrak asks clients to identify themselves; an unidentified/default
# User-Agent is one of the things that gets a 403 during rate limiting.
USER_AGENT = "kessler-satellite-api/0.1 (+https://github.com/Facewipe/kessler)"

# Status codes Celestrak returns when it is rate limiting a client. Retried
# with exponential backoff; any other error status is raised immediately.
RETRYABLE_STATUS_CODES = {403, 429}
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0

# On-disk cache so repeated runs within a short window reuse the last
# successful download instead of hitting Celestrak again.
DEFAULT_CACHE_PATH = Path(".cache/kessler/celestrak_gp.tle")
DEFAULT_CACHE_TTL_SECONDS = 900.0  # 15 minutes


def fetch_tle_text(
    group: str = DEFAULT_GROUP,
    timeout: float = 30.0,
    cache_path: str | Path | None = DEFAULT_CACHE_PATH,
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """Return the Celestrak GP dataset for `group` in 3-line TLE format.

    Serves a recent on-disk cached copy when available (`cache_path`,
    `cache_ttl_seconds`); otherwise downloads it, retrying with exponential
    backoff when Celestrak responds with 403/429 (rate limiting), and caches
    the result. Pass `cache_path=None` to always fetch over the network.
    """
    resolved_cache_path = Path(cache_path) if cache_path is not None else None

    if resolved_cache_path is not None:
        cached = _read_cache(resolved_cache_path, cache_ttl_seconds)
        if cached is not None:
            logger.info("Using cached Celestrak catalog at %s", resolved_cache_path)
            return cached

    text = _fetch_with_retry(group, timeout, max_retries)

    if resolved_cache_path is not None:
        _write_cache(resolved_cache_path, text)

    return text


def _fetch_with_retry(group: str, timeout: float, max_retries: int) -> str:
    """Download the catalog, retrying with exponential backoff on 403/429."""
    params = {"GROUP": group, "FORMAT": "tle"}
    headers = {"User-Agent": USER_AGENT}
    backoff = INITIAL_BACKOFF_SECONDS

    response = None
    for attempt in range(max_retries + 1):
        response = httpx.get(CELESTRAK_URL, params=params, timeout=timeout, headers=headers)
        if response.status_code not in RETRYABLE_STATUS_CODES:
            break
        if attempt < max_retries:
            logger.warning(
                "Celestrak returned %d, retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                backoff,
                attempt + 1,
                max_retries,
            )
            time.sleep(backoff)
            backoff *= 2

    response.raise_for_status()
    return response.text


def _read_cache(cache_path: Path, ttl_seconds: float) -> str | None:
    """Return the cached catalog text if it exists and is within `ttl_seconds`."""
    try:
        age_seconds = time.time() - cache_path.stat().st_mtime
    except FileNotFoundError:
        return None
    if age_seconds > ttl_seconds:
        return None
    return cache_path.read_text()


def _write_cache(cache_path: Path, text: str) -> None:
    """Write `text` to `cache_path`, creating parent directories as needed."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text)


def _iter_raw_records(text: str) -> list[tuple[str, str, str]]:
    """Split TLE text into raw `(name, line1, line2)` triples."""
    lines = [line for line in text.splitlines() if line.strip()]
    usable = len(lines) - len(lines) % 3
    return [(lines[i], lines[i + 1], lines[i + 2]) for i in range(0, usable, 3)]


def _parse_epoch(epoch_field: str) -> datetime:
    """Parse a TLE epoch field (`YYDDD.DDDDDDDD`) into a UTC datetime."""
    year_two_digit = int(epoch_field[0:2])
    day_of_year = float(epoch_field[2:])
    year = 2000 + year_two_digit if year_two_digit < 57 else 1900 + year_two_digit
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day_of_year - 1)


def _parse_record(name: str, line1: str, line2: str) -> SatelliteRecord | None:
    """Parse a single name/line1/line2 triple, or return `None` if malformed."""
    display_name = name.strip()

    if not line1.startswith("1 ") or not line2.startswith("2 "):
        logger.warning("Skipping malformed TLE record for %r: bad line prefix", display_name)
        return None

    try:
        norad_id_1 = int(line1[2:7])
        norad_id_2 = int(line2[2:7])
    except ValueError:
        logger.warning("Skipping malformed TLE record for %r: bad NORAD id", display_name)
        return None

    if norad_id_1 != norad_id_2:
        logger.warning(
            "Skipping malformed TLE record for %r: NORAD id mismatch (%d != %d)",
            display_name,
            norad_id_1,
            norad_id_2,
        )
        return None

    try:
        epoch_utc = _parse_epoch(line1[18:32])
    except ValueError:
        logger.warning("Skipping malformed TLE record for %r: bad epoch", display_name)
        return None

    return SatelliteRecord(
        name=display_name,
        norad_id=norad_id_1,
        line1=line1,
        line2=line2,
        epoch_utc=epoch_utc,
    )


def parse_tle_records(text: str) -> list[SatelliteRecord]:
    """Parse 3-line TLE groups into `SatelliteRecord`s, skipping malformed ones."""
    records = []
    for name, line1, line2 in _iter_raw_records(text):
        record = _parse_record(name, line1, line2)
        if record is not None:
            records.append(record)
    return records


def main() -> None:
    """Entry point for `python -m kessler.ingest`: fetch, upsert, print a summary."""
    logging.basicConfig(level=logging.INFO)

    db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)
    cache_path = os.environ.get("KESSLER_CACHE_PATH", DEFAULT_CACHE_PATH)
    text = fetch_tle_text(cache_path=cache_path)
    total = len(_iter_raw_records(text))
    records = parse_tle_records(text)

    conn = get_connection(db_path)
    try:
        result = upsert_records(conn, records)
    finally:
        conn.close()

    skipped = total - result.inserted - result.updated
    print(
        f"records fetched: {total} / inserted: {result.inserted} / "
        f"updated: {result.updated} / skipped: {skipped}"
    )


if __name__ == "__main__":
    main()
