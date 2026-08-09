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
