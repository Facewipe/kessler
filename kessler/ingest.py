"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from kessler.db import DEFAULT_DB_PATH, SatelliteRecord, get_connection, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_GROUP = "active"


def fetch_tle_text(group: str = DEFAULT_GROUP, timeout: float = 30.0) -> str:
    """Download the Celestrak GP dataset for `group` in 3-line TLE format."""
    params = {"GROUP": group, "FORMAT": "tle"}
    response = httpx.get(CELESTRAK_URL, params=params, timeout=timeout)
    response.raise_for_status()
    return response.text


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


@dataclass(frozen=True)
class IngestSummary:
    """Summary of one `run_ingest()` call."""

    fetched: int
    inserted: int
    updated: int
    skipped: int

    def __str__(self) -> str:
        return (
            f"records fetched: {self.fetched} / inserted: {self.inserted} / "
            f"updated: {self.updated} / skipped: {self.skipped}"
        )


def run_ingest(db_path: str | Path | None = None) -> IngestSummary:
    """Fetch the Celestrak catalog and upsert it into `db_path`.

    `db_path` defaults to the `KESSLER_DB_PATH` environment variable (or
    `DEFAULT_DB_PATH` if unset). Used both by the `python -m kessler.ingest`
    CLI and by the API's startup/periodic auto-ingest.
    """
    if db_path is None:
        db_path = os.environ.get("KESSLER_DB_PATH", DEFAULT_DB_PATH)

    text = fetch_tle_text()
    total = len(_iter_raw_records(text))
    records = parse_tle_records(text)

    conn = get_connection(db_path)
    try:
        result = upsert_records(conn, records)
    finally:
        conn.close()

    return IngestSummary(
        fetched=total,
        inserted=result.inserted,
        updated=result.updated,
        skipped=total - result.inserted - result.updated,
    )


def main() -> None:
    """Entry point for `python -m kessler.ingest`: fetch, upsert, print a summary."""
    logging.basicConfig(level=logging.INFO)
    print(run_ingest())


if __name__ == "__main__":
    main()
