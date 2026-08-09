"""Fetch TLE catalog data (Celestrak GP data) into local storage."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from kessler.db import TLERecord, get_connection, upsert_records

logger = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_GROUP = "active"
REQUEST_TIMEOUT = 30.0


def fetch_tle_text(group: str = DEFAULT_GROUP) -> str:
    """Download the Celestrak GP dataset for `group` in TLE format."""
    response = httpx.get(
        CELESTRAK_URL,
        params={"GROUP": group, "FORMAT": "tle"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def _parse_epoch(epoch_field: str) -> datetime:
    """Parse a TLE epoch field (YYDDD.DDDDDDDD) into a UTC datetime."""
    year_digits = int(epoch_field[:2])
    day_of_year = float(epoch_field[2:])
    year = 2000 + year_digits if year_digits < 57 else 1900 + year_digits
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day_of_year - 1)


def _parse_record(name_line: str, line1: str, line2: str) -> TLERecord | None:
    """Parse a single 3-line TLE group, or return None if it's malformed."""
    name = name_line.strip()
    if not line1.startswith("1 ") or not line2.startswith("2 "):
        logger.warning("Skipping malformed record for %r: bad line prefix", name)
        return None
    if len(line1) < 32 or len(line2) < 7:
        logger.warning("Skipping malformed record for %r: line too short", name)
        return None
    norad_id_1 = line1[2:7].strip()
    norad_id_2 = line2[2:7].strip()
    if not norad_id_1.isdigit() or norad_id_1 != norad_id_2:
        logger.warning("Skipping malformed record for %r: NORAD ID mismatch", name)
        return None
    try:
        epoch = _parse_epoch(line1[18:32])
    except ValueError:
        logger.warning("Skipping malformed record for %r: unparsable epoch", name)
        return None
    return TLERecord(
        name=name,
        norad_id=int(norad_id_1),
        line1=line1,
        line2=line2,
        epoch_utc=epoch.isoformat(),
    )


def _iter_groups(text: str) -> list[tuple[str, str, str]]:
    """Split TLE text into 3-line (name, line1, line2) groups."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    group_count = len(lines) // 3
    return [tuple(lines[i * 3 : i * 3 + 3]) for i in range(group_count)]


def parse_tle_records(text: str) -> list[TLERecord]:
    """Parse 3-line TLE groups into TLERecord objects.

    Malformed records (bad line prefix, mismatched NORAD ID between the two
    lines, or an unparsable epoch) are skipped with a logged warning rather
    than raising.
    """
    records = []
    for name_line, line1, line2 in _iter_groups(text):
        record = _parse_record(name_line, line1, line2)
        if record is not None:
            records.append(record)
    return records


@dataclass
class IngestSummary:
    """Summary counts for a single ingest run."""

    fetched: int
    inserted: int
    updated: int
    skipped: int


def run_ingest(text: str) -> IngestSummary:
    """Parse `text` and upsert the resulting records into the configured DB."""
    total_groups = len(_iter_groups(text))
    records = parse_tle_records(text)
    malformed = total_groups - len(records)

    fetched_at = datetime.now(UTC).isoformat()
    conn = get_connection()
    try:
        result = upsert_records(conn, records, fetched_at)
    finally:
        conn.close()

    return IngestSummary(
        fetched=total_groups,
        inserted=result.inserted,
        updated=result.updated,
        skipped=malformed + result.skipped,
    )


def main() -> None:
    """Entry point for `python -m kessler.ingest`."""
    logging.basicConfig(level=logging.INFO)
    text = fetch_tle_text()
    summary = run_ingest(text)
    print(
        f"records fetched: {summary.fetched}, inserted: {summary.inserted}, "
        f"updated: {summary.updated}, skipped: {summary.skipped}"
    )


if __name__ == "__main__":
    main()
