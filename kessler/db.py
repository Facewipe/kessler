"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "kessler.db"

# Bumped whenever `_SCHEMA` changes in a way existing on-disk databases need
# to migrate to (new/renamed/removed columns). Checked against SQLite's
# built-in `PRAGMA user_version` so migrations only run once per database.
CURRENT_SCHEMA_VERSION = 1

_EXPECTED_COLUMNS = ("norad_id", "name", "line1", "line2", "epoch_utc", "fetched_at")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS satellites (
    norad_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL,
    epoch_utc TEXT,
    fetched_at TEXT
);
"""


@dataclass(frozen=True)
class SatelliteRecord:
    """A stored TLE record for one satellite."""

    norad_id: int
    name: str
    line1: str
    line2: str
    epoch_utc: datetime | None = None


@dataclass
class UpsertResult:
    """Summary of an `upsert_records()` call."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection to `db_path`, creating or migrating the schema as needed.

    `check_same_thread=False` because callers (the FastAPI dependency and
    test fixtures via `TestClient`) may hand a single connection across
    threads. This app never accesses one connection from multiple threads
    concurrently, only sequentially, so relaxing the check is safe.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _ensure_schema_current(conn)
    return conn


def _ensure_schema_current(conn: sqlite3.Connection) -> None:
    """Create the `satellites` table if missing, or migrate it if out of date.

    A pre-existing database created before `epoch_utc`/`fetched_at` were
    added has no way to pick those columns up via `CREATE TABLE IF NOT
    EXISTS`, which silently no-ops on an already-existing table. Track the
    schema generation with SQLite's built-in `PRAGMA user_version` so this
    check is a cheap integer comparison on the common case (already
    migrated), and only fall back to inspecting `PRAGMA table_info` -- and
    rebuilding the table -- when the version is behind.
    """
    conn.execute(_SCHEMA)

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < CURRENT_SCHEMA_VERSION:
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(satellites)")}
        if not set(_EXPECTED_COLUMNS).issubset(existing_columns):
            _migrate_satellites_table(conn)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    conn.commit()


def _migrate_satellites_table(conn: sqlite3.Connection) -> None:
    """Rebuild `satellites` under the current schema, preserving compatible data."""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(satellites)")}
    common_columns = [c for c in _EXPECTED_COLUMNS if c in existing_columns]

    conn.execute("ALTER TABLE satellites RENAME TO satellites_old")
    conn.execute(_SCHEMA)
    if common_columns:
        columns_sql = ", ".join(common_columns)
        conn.execute(
            f"INSERT INTO satellites ({columns_sql}) SELECT {columns_sql} FROM satellites_old"
        )
    conn.execute("DROP TABLE satellites_old")
    logger.info(
        "Migrated satellites table to schema version %d (kept columns: %s)",
        CURRENT_SCHEMA_VERSION,
        ", ".join(common_columns),
    )


def upsert_satellite(conn: sqlite3.Connection, record: SatelliteRecord) -> None:
    """Insert or unconditionally replace a satellite's TLE record.

    For epoch-aware bulk ingestion that skips stale TLEs, use
    `upsert_records()` instead.
    """
    conn.execute(
        "INSERT OR REPLACE INTO satellites (norad_id, name, line1, line2, epoch_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            record.norad_id,
            record.name,
            record.line1,
            record.line2,
            record.epoch_utc.isoformat() if record.epoch_utc is not None else None,
        ),
    )
    conn.commit()


def get_satellite(conn: sqlite3.Connection, norad_id: int) -> SatelliteRecord | None:
    """Fetch a satellite's TLE record by NORAD catalog ID, or None if unknown."""
    row = conn.execute(
        "SELECT norad_id, name, line1, line2, epoch_utc FROM satellites WHERE norad_id = ?",
        (norad_id,),
    ).fetchone()
    if row is None:
        return None
    return _record_from_row(row)


def list_satellites(conn: sqlite3.Connection) -> list[SatelliteRecord]:
    """Fetch all stored satellite TLE records."""
    rows = conn.execute("SELECT norad_id, name, line1, line2, epoch_utc FROM satellites").fetchall()
    return [_record_from_row(row) for row in rows]


def _record_from_row(row: tuple[int, str, str, str, str | None]) -> SatelliteRecord:
    norad_id, name, line1, line2, epoch_utc = row
    return SatelliteRecord(
        norad_id=norad_id,
        name=name,
        line1=line1,
        line2=line2,
        epoch_utc=datetime.fromisoformat(epoch_utc) if epoch_utc is not None else None,
    )


def upsert_records(conn: sqlite3.Connection, records: list[SatelliteRecord]) -> UpsertResult:
    """Upsert TLE records keyed on `norad_id`, skipping stale epochs.

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
            "SELECT epoch_utc FROM satellites WHERE norad_id = ?", (record.norad_id,)
        ).fetchone()

        if row is None or row[0] is None:
            conn.execute(
                "INSERT OR REPLACE INTO satellites "
                "(norad_id, name, line1, line2, epoch_utc, fetched_at) "
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
                "UPDATE satellites SET name = ?, line1 = ?, line2 = ?, epoch_utc = ?, "
                "fetched_at = ? WHERE norad_id = ?",
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
