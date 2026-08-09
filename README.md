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

`python -m kessler.ingest` downloads the active-satellites Celestrak GP dataset in TLE format, parses it, and upserts records into a local SQLite database keyed on `norad_id`. Records are only replaced when the freshly fetched TLE has a strictly newer epoch than what is stored, so running the command repeatedly never creates duplicate rows. Malformed records are skipped with a logged warning instead of failing the run.

The database path is configurable via the `KESSLER_DB_PATH` environment variable (default: `kessler.db` in the working directory).

## API

### Get a satellite's current position

```bash
curl "http://localhost:8000/satellites/25544/position"
```

Optionally pass an ISO 8601 UTC timestamp via `at` (defaults to now):

```bash
curl "http://localhost:8000/satellites/25544/position?at=2026-08-09T12:00:00Z"
```

Example response:

```json
{
  "norad_id": 25544,
  "name": "ISS (ZARYA)",
  "at": "2026-08-09T12:00:00+00:00",
  "lat": 12.345678,
  "lon": -45.678901,
  "alt_km": 420.123,
  "epoch_utc": "2026-08-08T03:15:22.123456+00:00",
  "epoch_age_hours": 32.744,
  "stale": false
}
```

Position is computed via SGP4 from the satellite's most recently ingested
TLE. Accuracy is roughly km-level near the TLE epoch and degrades as the TLE
ages; `epoch_age_hours` reports that age, and `stale` is `true` once it
exceeds 72 hours. Only geometric position is reported here — no covariance or
collision probability.

Returns `404` for an unknown `norad_id` and `422` for an invalid `at`
timestamp.

### Screen a satellite for conjunctions

```bash
curl "http://localhost:8000/conjunctions/25544?hours=72&threshold_km=10"
```

`hours` (1-168, default 72) sets the screening window length from now;
`threshold_km` (1-50, default 10) sets both the coarse orbit-overlap buffer
and the candidate miss-distance bound.

Example response:

```json
{
  "disclaimer": "Geometric screening on public TLEs (SGP4), not a collision probability. No covariance is used; treat results as a geometric proximity estimate only.",
  "target_norad_id": 25544,
  "target_name": "ISS (ZARYA)",
  "window_start_utc": "2026-08-09T12:00:00+00:00",
  "window_end_utc": "2026-08-12T12:00:00+00:00",
  "threshold_km": 10.0,
  "conjunctions": [
    {
      "other_norad_id": 43205,
      "other_name": "STARLINK-1007",
      "tca_utc": "2026-08-10T03:12:47+00:00",
      "miss_distance_km": 3.842,
      "target_epoch_age_hours": 5.1,
      "other_epoch_age_hours": 12.4
    }
  ]
}
```

Screening applies a coarse apogee/perigee overlap filter to prune the
catalog, then propagates surviving pairs on a coarse (60 s) grid and refines
local minima (1 s steps) to find time of closest approach (TCA) and miss
distance. Results are sorted by miss distance, ascending. As with the
position endpoint, this reports **geometric miss distance only** — never a
collision probability.

Returns `404` for an unknown `norad_id` and `422` for `hours` or
`threshold_km` outside their allowed ranges.
