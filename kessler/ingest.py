"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from kessler.db import TLERecord, get_connection, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
REQUEST_TIMEOUT_SECONDS = 30.0


def fetch_tle_text(url: str = CELESTRAK_URL, timeout: float = REQUEST_TIMEOUT_SECONDS) -> str:
    """Download the Celestrak GP dataset in TLE format as raw text."""
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def _parse_epoch(epoch_field: str) -> datetime:
    """Parse a TLE epoch field (`YYDDD.DDDDDDDD`) into a UTC datetime."""
    year_2digit = int(epoch_field[:2])
    day_of_year = float(epoch_field[2:])
    year = 2000 + year_2digit if year_2digit < 57 else 1900 + year_2digit
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)


def _iter_record_groups(text: str) -> list[tuple[str, str, str]]:
    """Group non-blank lines of TLE text into (name, line1, line2) triples."""
    lines = [line for line in text.splitlines() if line.strip()]
    leftover = len(lines) % 3
    if leftover:
        logger.warning("Ignoring %d trailing line(s) that don't form a full TLE record", leftover)
        lines = lines[: len(lines) - leftover]
    return [tuple(lines[i : i + 3]) for i in range(0, len(lines), 3)]


def _parse_group(name_line: str, line1: str, line2: str) -> TLERecord | None:
    """Parse one (name, line1, line2) group into a `TLERecord`, or None if malformed."""
    name = name_line.strip()

    if not line1.startswith("1 ") or not line2.startswith("2 "):
        logger.warning("Skipping malformed TLE record for %r: bad line prefix", name)
        return None

    try:
        norad_id_1 = int(line1[2:7])
        norad_id_2 = int(line2[2:7])
    except ValueError:
        logger.warning("Skipping malformed TLE record for %r: unparsable NORAD ID", name)
        return None

    if norad_id_1 != norad_id_2:
        logger.warning(
            "Skipping malformed TLE record for %r: NORAD ID mismatch (%d != %d)",
            name,
            norad_id_1,
            norad_id_2,
        )
        return None

    try:
        epoch_utc = _parse_epoch(line1[18:32])
    except ValueError:
        logger.warning("Skipping malformed TLE record for %r: unparsable epoch", name)
        return None

    return TLERecord(name=name, norad_id=norad_id_1, line1=line1, line2=line2, epoch_utc=epoch_utc)


def _parse_groups(groups: list[tuple[str, str, str]]) -> list[TLERecord]:
    return [record for group in groups if (record := _parse_group(*group)) is not None]


def parse_tle_records(text: str) -> list[TLERecord]:
    """Parse Celestrak 3-line TLE text into `TLERecord` objects.

    Malformed groups (bad line prefixes, mismatched NORAD IDs between line 1
    and line 2, or an unparsable epoch) are skipped with a logged warning
    instead of raising.
    """
    return _parse_groups(_iter_record_groups(text))


def main() -> None:
    """Entry point for `python -m kessler.ingest`.

    Fetches the active-satellites Celestrak GP dataset, parses it, upserts
    the records into SQLite, and prints a one-line summary.
    """
    logging.basicConfig(level=logging.INFO)

    text = fetch_tle_text()
    groups = _iter_record_groups(text)
    records = _parse_groups(groups)
    skipped_parsing = len(groups) - len(records)

    conn = get_connection()
    try:
        result = upsert_records(conn, records)
    finally:
        conn.close()

    print(
        f"records fetched: {len(groups)}, inserted: {result.inserted}, "
        f"updated: {result.updated}, skipped: {skipped_parsing + result.skipped}"
    )


if __name__ == "__main__":
    main()
