"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB_PATH = "./kessler.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tle (
    norad_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL,
    epoch_utc TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


@dataclass
class TLERecord:
    """A single parsed TLE record ready for storage."""

    name: str
    norad_id: int
    line1: str
    line2: str
    epoch_utc: datetime


@dataclass
class UpsertResult:
    """Summary counts from an upsert_records() call."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def get_db_path() -> str:
    """Return the SQLite DB path from KESSLER_DB, defaulting to ./kessler.db."""
    return os.environ.get("KESSLER_DB", DEFAULT_DB_PATH)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection at db_path, creating the schema if needed."""
    path = db_path if db_path is not None else get_db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_records(conn: sqlite3.Connection, records: list[TLERecord]) -> UpsertResult:
    """Insert new records and update existing ones only if the epoch is newer.

    Rows are keyed on norad_id. A record whose epoch is not strictly newer
    than what is already stored is counted as skipped, so re-running ingest
    never duplicates rows or regresses to a stale TLE.
    """
    result = UpsertResult()
    fetched_at = datetime.now(UTC).isoformat()

    for record in records:
        cursor = conn.execute("SELECT epoch_utc FROM tle WHERE norad_id = ?", (record.norad_id,))
        existing = cursor.fetchone()

        if existing is None:
            _insert(conn, record, fetched_at)
            result.inserted += 1
        elif record.epoch_utc > datetime.fromisoformat(existing[0]):
            _update(conn, record, fetched_at)
            result.updated += 1
        else:
            result.skipped += 1

    conn.commit()
    return result


def _insert(conn: sqlite3.Connection, record: TLERecord, fetched_at: str) -> None:
    conn.execute(
        "INSERT INTO tle (norad_id, name, line1, line2, epoch_utc, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            record.norad_id,
            record.name,
            record.line1,
            record.line2,
            record.epoch_utc.isoformat(),
            fetched_at,
        ),
    )


def _update(conn: sqlite3.Connection, record: TLERecord, fetched_at: str) -> None:
    conn.execute(
        "UPDATE tle SET name = ?, line1 = ?, line2 = ?, epoch_utc = ?, fetched_at = ? "
        "WHERE norad_id = ?",
        (
            record.name,
            record.line1,
            record.line2,
            record.epoch_utc.isoformat(),
            fetched_at,
            record.norad_id,
        ),
    )
