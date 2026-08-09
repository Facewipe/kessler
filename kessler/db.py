"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

DEFAULT_DB_PATH = "./kessler.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS satellites (
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


def get_db_path() -> str:
    """Return the configured SQLite database path.

    Controlled by the KESSLER_DB env var, defaulting to ./kessler.db.
    """
    return os.environ.get("KESSLER_DB", DEFAULT_DB_PATH)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection to `db_path` (or the configured default), creating the schema."""
    conn = sqlite3.connect(db_path or get_db_path())
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_records(
    conn: sqlite3.Connection, records: Iterable[TLERecord], fetched_at: datetime
) -> dict[str, int]:
    """Upsert TLE records keyed on norad_id, keeping the newest epoch on conflict.

    A record whose norad_id is not yet in the table is inserted. A record for
    an existing norad_id replaces the stored row only if its epoch is newer;
    otherwise it is left untouched. Returns a summary of how many records
    were inserted / updated / skipped.
    """
    summary = {"inserted": 0, "updated": 0, "skipped": 0}
    fetched_at_iso = fetched_at.isoformat()

    for record in records:
        row = conn.execute(
            "SELECT epoch_utc FROM satellites WHERE norad_id = ?", (record.norad_id,)
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO satellites (norad_id, name, line1, line2, epoch_utc, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.norad_id,
                    record.name,
                    record.line1,
                    record.line2,
                    record.epoch_utc.isoformat(),
                    fetched_at_iso,
                ),
            )
            summary["inserted"] += 1
            continue

        existing_epoch = datetime.fromisoformat(row[0])
        if record.epoch_utc <= existing_epoch:
            summary["skipped"] += 1
            continue

        conn.execute(
            "UPDATE satellites SET name = ?, line1 = ?, line2 = ?, epoch_utc = ?, fetched_at = ? "
            "WHERE norad_id = ?",
            (
                record.name,
                record.line1,
                record.line2,
                record.epoch_utc.isoformat(),
                fetched_at_iso,
                record.norad_id,
            ),
        )
        summary["updated"] += 1

    conn.commit()
    return summary
