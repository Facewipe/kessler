"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = "./kessler.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tles (
    norad_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL,
    epoch_utc TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class TLERecord:
    """A single parsed TLE record."""

    name: str
    norad_id: int
    line1: str
    line2: str
    epoch_utc: datetime


@dataclass(frozen=True)
class UpsertResult:
    """Summary of an `upsert_records` call."""

    inserted: int
    updated: int
    skipped: int


def get_db_path() -> Path:
    """Return the configured SQLite database path.

    Reads the `KESSLER_DB` environment variable, falling back to
    `./kessler.db` if it is not set.
    """
    return Path(os.environ.get("KESSLER_DB", DEFAULT_DB_PATH))


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection to `db_path`, creating the schema if needed.

    Defaults to `get_db_path()` when `db_path` is not given.
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_records(
    conn: sqlite3.Connection,
    records: Iterable[TLERecord],
    fetched_at: datetime | None = None,
) -> UpsertResult:
    """Insert or update TLE records, keyed on `norad_id`.

    A record replaces the stored row only if its epoch is strictly newer
    than the currently stored epoch (or no row exists yet for that
    `norad_id`). Records whose epoch is not newer than what is already
    stored are counted as skipped, so re-running ingestion never creates
    duplicates or regresses to a stale TLE.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)
    fetched_at_str = fetched_at.isoformat()
    inserted = updated = skipped = 0

    for record in records:
        row = conn.execute(
            "SELECT epoch_utc FROM tles WHERE norad_id = ?", (record.norad_id,)
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO tles (norad_id, name, line1, line2, epoch_utc, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.norad_id,
                    record.name,
                    record.line1,
                    record.line2,
                    record.epoch_utc.isoformat(),
                    fetched_at_str,
                ),
            )
            inserted += 1
            continue

        existing_epoch = datetime.fromisoformat(row[0])
        if record.epoch_utc <= existing_epoch:
            skipped += 1
            continue

        conn.execute(
            "UPDATE tles SET name = ?, line1 = ?, line2 = ?, epoch_utc = ?, fetched_at = ? "
            "WHERE norad_id = ?",
            (
                record.name,
                record.line1,
                record.line2,
                record.epoch_utc.isoformat(),
                fetched_at_str,
                record.norad_id,
            ),
        )
        updated += 1

    conn.commit()
    return UpsertResult(inserted=inserted, updated=updated, skipped=skipped)
