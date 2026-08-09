"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB_PATH = "./kessler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tle (
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
    """A single parsed TLE record ready for storage."""

    name: str
    norad_id: int
    line1: str
    line2: str
    epoch_utc: datetime


@dataclass
class UpsertResult:
    """Summary of an `upsert_records()` call."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def get_db_path() -> Path:
    """Return the configured SQLite DB path (`KESSLER_DB` env var, or the default)."""
    return Path(os.environ.get("KESSLER_DB", DEFAULT_DB_PATH))


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection, creating the schema if it doesn't exist yet."""
    path = db_path if db_path is not None else get_db_path()
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def upsert_records(conn: sqlite3.Connection, records: list[TLERecord]) -> UpsertResult:
    """Upsert TLE records keyed on `norad_id`.

    A record with a `norad_id` not yet in the database is always inserted.
    A record for a `norad_id` already in the database replaces the stored
    row only when its epoch is strictly newer than the stored epoch;
    otherwise it is counted as skipped. This guarantees that re-running
    ingestion never creates duplicate rows and never regresses a stored
    TLE to an older or identical epoch.
    """
    result = UpsertResult()
    fetched_at = datetime.now(UTC).isoformat()

    for record in records:
        row = conn.execute(
            "SELECT epoch_utc FROM tle WHERE norad_id = ?", (record.norad_id,)
        ).fetchone()

        if row is None:
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
            result.inserted += 1
            continue

        existing_epoch = datetime.fromisoformat(row[0])
        if record.epoch_utc > existing_epoch:
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
            result.updated += 1
        else:
            result.skipped += 1

    conn.commit()
    return result
