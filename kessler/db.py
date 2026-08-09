"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

import os
import sqlite3
from dataclasses import dataclass
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
)
"""


@dataclass(frozen=True)
class TLERecord:
    """A single parsed TLE record."""

    name: str
    norad_id: int
    line1: str
    line2: str
    epoch_utc: str


@dataclass
class UpsertResult:
    """Summary counts for a single upsert_records() call."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def get_db_path() -> str:
    """Return the SQLite database path from KESSLER_DB, or the default."""
    return os.environ.get("KESSLER_DB", DEFAULT_DB_PATH)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection, creating the schema if it doesn't exist yet."""
    path = db_path if db_path is not None else get_db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_records(
    conn: sqlite3.Connection,
    records: list[TLERecord],
    fetched_at: str,
) -> UpsertResult:
    """Upsert TLE records keyed on norad_id.

    A record whose epoch is newer than the stored one replaces it; a record
    with an equal or older epoch is left in place and counted as skipped.
    """
    result = UpsertResult()
    for record in records:
        row = conn.execute(
            "SELECT epoch_utc FROM tles WHERE norad_id = ?",
            (record.norad_id,),
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
                    record.epoch_utc,
                    fetched_at,
                ),
            )
            result.inserted += 1
        elif record.epoch_utc > row[0]:
            conn.execute(
                "UPDATE tles SET name = ?, line1 = ?, line2 = ?, epoch_utc = ?, fetched_at = ? "
                "WHERE norad_id = ?",
                (
                    record.name,
                    record.line1,
                    record.line2,
                    record.epoch_utc,
                    fetched_at,
                    record.norad_id,
                ),
            )
            result.updated += 1
        else:
            result.skipped += 1
    conn.commit()
    return result
