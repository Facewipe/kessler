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
dataset in TLE format, parses it, and upserts records into a local SQLite
database keyed on `norad_id`. Records are only replaced when the freshly
fetched TLE has a strictly newer epoch than what is stored, so running the
command repeatedly never creates duplicate rows. Malformed records are
skipped with a logged warning instead of failing the run.

The database path is configurable via the `KESSLER_DB` environment variable
(default: `./kessler.db`).
