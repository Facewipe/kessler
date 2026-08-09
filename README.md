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

## Ingestion

`python -m kessler.ingest` downloads the Celestrak GP dataset for the
"active" satellite group in TLE format, parses each 3-line record, and
upserts it into a local SQLite database keyed on NORAD ID. Malformed
records (bad line prefix, mismatched NORAD ID between lines, unparsable
epoch) are skipped with a logged warning rather than aborting the run. An
existing row is only overwritten when the newly fetched TLE has a strictly
newer epoch, so running the command repeatedly never creates duplicates or
regresses to a stale TLE.

The database path is configurable via the `KESSLER_DB` environment
variable (default: `./kessler.db`).
