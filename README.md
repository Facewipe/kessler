# kessler
Satellite conjunction screening API built on open orbital data

## Development setup

```bash
pip install -e ".[dev]"      # setup
pytest                        # tests
ruff check . && ruff format --check .   # lint
uvicorn kessler.api:app --reload        # run locally
python -m kessler.ingest      # fetch TLE catalog into SQLite
```

## TLE ingestion

`python -m kessler.ingest` downloads the active-satellites Celestrak GP
dataset in TLE format, parses each record, and upserts it into a local
SQLite database keyed on NORAD catalog ID. Records are only replaced when
the newly fetched TLE has a strictly newer epoch, so running the command
repeatedly (e.g. on a schedule) never creates duplicates or regresses to a
stale TLE. Malformed records (bad line prefixes, mismatched NORAD IDs
between line 1 and line 2, or an unparsable epoch) are skipped with a
logged warning instead of aborting the whole run.

The database file location is configurable via the `KESSLER_DB`
environment variable (default `./kessler.db`).
