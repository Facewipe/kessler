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

## Catalog ingestion

`python -m kessler.ingest` downloads the active-satellites GP dataset from
Celestrak in TLE format, parses each record, and upserts it into a SQLite
database keyed on NORAD catalog ID. Records for a NORAD ID that already
exists are only replaced if the newly fetched TLE has a newer epoch, so
running the command repeatedly (e.g. on a schedule) does not create
duplicate rows and never overwrites a newer TLE with a stale one.

The database file path is configurable via the `KESSLER_DB` environment
variable (see `.env.example`); it defaults to `./kessler.db`.

Malformed records (bad line prefixes, mismatched NORAD IDs between lines,
unparsable epochs) are skipped with a logged warning rather than aborting
the whole run.
