"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

import logging
from datetime import UTC, datetime, timedelta

import httpx

from kessler.db import TLERecord, get_connection, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_GROUP = "active"


def fetch_tle_text(group: str = DEFAULT_GROUP) -> str:
    """Download the Celestrak GP dataset for the given group in TLE format."""
    response = httpx.get(CELESTRAK_URL, params={"GROUP": group, "FORMAT": "tle"}, timeout=30)
    response.raise_for_status()
    return response.text


def _non_blank_lines(text: str) -> list[str]:
    return [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]


def parse_tle_records(text: str) -> list[TLERecord]:
    """Parse Celestrak GP TLE text into TLERecord objects.

    Text is grouped into 3-line records (name, line1, line2). Malformed
    groups are skipped with a logged warning instead of raising, so one
    bad record does not abort the whole ingest run.
    """
    lines = _non_blank_lines(text)
    records: list[TLERecord] = []

    for start in range(0, len(lines), 3):
        chunk = lines[start : start + 3]
        if len(chunk) < 3:
            logger.warning("Skipping incomplete trailing record at end of input")
            break

        name_line, line1, line2 = chunk
        record = _parse_record(name_line, line1, line2)
        if record is not None:
            records.append(record)

    return records


def _parse_record(name_line: str, line1: str, line2: str) -> TLERecord | None:
    name = name_line.strip()

    if not line1.startswith("1 ") or not line2.startswith("2 "):
        logger.warning("Skipping malformed record %r: bad line prefix", name)
        return None

    if len(line1) < 32 or len(line2) < 68:
        logger.warning("Skipping malformed record %r: line too short", name)
        return None

    norad_id_1 = line1[2:7].strip()
    norad_id_2 = line2[2:7].strip()
    if not norad_id_1.isdigit() or norad_id_1 != norad_id_2:
        logger.warning("Skipping malformed record %r: NORAD ID mismatch", name)
        return None

    try:
        epoch_utc = _parse_epoch(line1[18:32])
    except ValueError:
        logger.warning("Skipping malformed record %r: unparsable epoch", name)
        return None

    return TLERecord(
        name=name,
        norad_id=int(norad_id_1),
        line1=line1,
        line2=line2,
        epoch_utc=epoch_utc,
    )


def _parse_epoch(epoch_field: str) -> datetime:
    """Parse a TLE epoch field (YYDDD.DDDDDDDD) into a UTC datetime."""
    year_2digit = int(epoch_field[:2])
    day_of_year = float(epoch_field[2:])
    year = 2000 + year_2digit if year_2digit < 57 else 1900 + year_2digit
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day_of_year - 1)


def main() -> None:
    """Entry point for `python -m kessler.ingest`: fetch, parse, and upsert."""
    logging.basicConfig(level=logging.INFO)

    text = fetch_tle_text()
    fetched = len(_non_blank_lines(text)) // 3

    records = parse_tle_records(text)
    conn = get_connection()
    try:
        result = upsert_records(conn, records)
    finally:
        conn.close()

    skipped = (fetched - len(records)) + result.skipped
    print(
        f"records fetched: {fetched}, inserted: {result.inserted}, "
        f"updated: {result.updated}, skipped: {skipped}"
    )


if __name__ == "__main__":
    main()
