"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from kessler.db import TLERecord, get_connection, get_db_path, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
REQUEST_TIMEOUT_S = 30.0


def fetch_tle_text(url: str = CELESTRAK_URL) -> str:
    """Download the raw TLE-format text for a Celestrak GP group."""
    response = httpx.get(url, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.text


def _parse_epoch(epoch_field: str) -> datetime:
    """Convert a TLE epoch field (YYDDD.DDDDDDDD) into a UTC datetime."""
    epoch_field = epoch_field.strip()
    year_two_digit = int(epoch_field[:2])
    day_of_year = float(epoch_field[2:])
    year = 2000 + year_two_digit if year_two_digit < 57 else 1900 + year_two_digit

    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)


def _parse_record(name_line: str, line1: str, line2: str) -> TLERecord | None:
    """Parse a single 3-line TLE record, returning None (and logging) if malformed."""
    name = name_line.strip()

    if not line1.startswith("1 ") or not line2.startswith("2 "):
        logger.warning("Skipping malformed record for %r: bad line prefix", name)
        return None

    try:
        norad_id_1 = int(line1[2:7])
        norad_id_2 = int(line2[2:7])
    except ValueError:
        logger.warning("Skipping malformed record for %r: unparsable NORAD ID", name)
        return None

    if norad_id_1 != norad_id_2:
        logger.warning(
            "Skipping malformed record for %r: NORAD ID mismatch between lines (%d != %d)",
            name,
            norad_id_1,
            norad_id_2,
        )
        return None

    try:
        epoch_utc = _parse_epoch(line1[18:32])
    except ValueError:
        logger.warning("Skipping malformed record for %r: unparsable epoch", name)
        return None

    return TLERecord(
        name=name,
        norad_id=norad_id_1,
        line1=line1,
        line2=line2,
        epoch_utc=epoch_utc,
    )


def parse_tle_records(text: str) -> list[TLERecord]:
    """Parse Celestrak TLE-format text (3 lines per record: name, line1, line2).

    Malformed records are skipped with a logged warning instead of raising.
    """
    lines = [line.rstrip("\n\r") for line in text.splitlines() if line.strip()]
    records: list[TLERecord] = []

    for i in range(0, len(lines), 3):
        group = lines[i : i + 3]
        if len(group) < 3:
            logger.warning("Skipping incomplete trailing record: %r", group)
            break

        record = _parse_record(*group)
        if record is not None:
            records.append(record)

    return records


def main() -> None:
    """Entry point for `python -m kessler.ingest`: fetch, parse and upsert the full catalog."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    text = fetch_tle_text()
    total_records = len([line for line in text.splitlines() if line.strip()]) // 3
    records = parse_tle_records(text)
    malformed_count = total_records - len(records)

    conn = get_connection(get_db_path())
    try:
        summary = upsert_records(conn, records, fetched_at=datetime.now(timezone.utc))
    finally:
        conn.close()

    skipped = malformed_count + summary["skipped"]
    print(
        f"records fetched: {total_records}, inserted: {summary['inserted']}, "
        f"updated: {summary['updated']}, skipped: {skipped}"
    )


if __name__ == "__main__":
    main()
