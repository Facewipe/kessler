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

`python -m kessler.ingest` downloads the current Celestrak GP dataset for
the active-satellites group in TLE format, parses it, and upserts the
records into a local SQLite database keyed on `norad_id`. Records with a
newer epoch replace older ones; malformed records are skipped and logged
as warnings rather than aborting the run. Running it again later only
updates satellites whose TLE has a newer epoch — it never creates
duplicates.

The database path defaults to `./kessler.db` and can be overridden with the
`KESSLER_DB` environment variable.
