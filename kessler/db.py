"""Thin SQLite data layer (MVP storage, replaceable by Postgres later)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = "kessler.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS satellites (
    norad_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class SatelliteRecord:
    """A stored TLE record for one satellite."""

    norad_id: int
    name: str
    line1: str
    line2: str


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection to `db_path`, creating the schema if needed.

    `check_same_thread=False` because callers (the FastAPI dependency and
    test fixtures via `TestClient`) may hand a single connection across
    threads. This app never accesses one connection from multiple threads
    concurrently, only sequentially, so relaxing the check is safe.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def upsert_satellite(conn: sqlite3.Connection, record: SatelliteRecord) -> None:
    """Insert or replace a satellite's TLE record."""
    conn.execute(
        "INSERT OR REPLACE INTO satellites (norad_id, name, line1, line2) VALUES (?, ?, ?, ?)",
        (record.norad_id, record.name, record.line1, record.line2),
    )
    conn.commit()


def get_satellite(conn: sqlite3.Connection, norad_id: int) -> SatelliteRecord | None:
    """Fetch a satellite's TLE record by NORAD catalog ID, or None if unknown."""
    row = conn.execute(
        "SELECT norad_id, name, line1, line2 FROM satellites WHERE norad_id = ?",
        (norad_id,),
    ).fetchone()
    if row is None:
        return None
    return SatelliteRecord(*row)


def list_satellites(conn: sqlite3.Connection) -> list[SatelliteRecord]:
    """Fetch all stored satellite TLE records."""
    rows = conn.execute("SELECT norad_id, name, line1, line2 FROM satellites").fetchall()
    return [SatelliteRecord(*row) for row in rows]
