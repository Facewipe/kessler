# kessler

Satellite conjunction screening API built on open orbital data. Give it a
NORAD catalog ID and a time, get back a geodetic position computed from the
satellite's most recent TLE via SGP4 — with the TLE's epoch age surfaced so
you know how much to trust it.

See [docs/accuracy.md](docs/accuracy.md) for what SGP4/TLE accuracy actually
means and why this API reports geometric miss distance, not collision
probability.

## Quickstart

### 1. Install

```bash
git clone <this-repo-url> && cd kessler
pip install -e ".[dev]"
```

### 2. Load some satellite data

Catalog ingestion from Celestrak (`python -m kessler.ingest`) is still under
development (tracked separately) and currently raises `NotImplementedError`.
Until it lands, seed one demo satellite directly into the local SQLite
catalog with the pinned reference TLE used throughout this repo's test
suite:

```bash
python -c "
from kessler.db import SatelliteRecord, get_connection, upsert_satellite

conn = get_connection('kessler.db')
upsert_satellite(conn, SatelliteRecord(
    norad_id=5,
    name='SGP4-VER TEST SATELLITE 5',
    line1='1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
    line2='2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667',
))
conn.close()
"
```

### 3. Run

```bash
uvicorn kessler.api:app --reload
```

The API is now open on `http://localhost:8000`. Interactive docs (Swagger
UI) are at `http://localhost:8000/docs`.

By default the API has no authentication (dev mode). To require an API key,
set `KESSLER_API_KEYS` before starting the server — see
[Authentication](#authentication) below.

## API

### Health check

```bash
curl "http://localhost:8000/health"
```

```json
{"status": "ok"}
```

### Get a satellite's current position

```bash
curl "http://localhost:8000/satellites/5/position"
```

### Get a satellite's position at a specific time

Pass an ISO 8601 UTC timestamp via `at` (defaults to now):

```bash
curl "http://localhost:8000/satellites/5/position?at=2000-06-28T00:50:19Z"
```

Example response:

```json
{
  "norad_id": 5,
  "name": "SGP4-VER TEST SATELLITE 5",
  "at": "2000-06-28T00:50:19+00:00",
  "lat": 12.345678,
  "lon": -45.678901,
  "alt_km": 420.123,
  "epoch_utc": "2000-06-27T18:50:19.733568+00:00",
  "epoch_age_hours": 6.0,
  "stale": false
}
```

Position is computed via SGP4 from the satellite's most recently ingested
TLE. Accuracy is roughly km-level near the TLE epoch and degrades as the TLE
ages; `epoch_age_hours` reports that age, and `stale` is `true` once it
exceeds 72 hours. Only geometric position is reported here — no covariance or
collision probability. See [docs/accuracy.md](docs/accuracy.md) for details.

Returns `404` for an unknown `norad_id` and `422` for an invalid `at`
timestamp.

### Python example

```python
import requests

response = requests.get("http://localhost:8000/satellites/5/position")
response.raise_for_status()
data = response.json()

print(f"{data['name']}: {data['lat']}, {data['lon']} ({data['alt_km']} km)")
if data["stale"]:
    print(f"Warning: TLE is {data['epoch_age_hours']:.1f}h old, position may be inaccurate")
```

## Authentication

By default the API is open — no key required. To lock it down, set
`KESSLER_API_KEYS` to a comma-separated list of keys before starting the
server:

```bash
export KESSLER_API_KEYS="dev-key-1,dev-key-2"
uvicorn kessler.api:app --reload
```

With this set, every endpoint except `/health` requires a matching
`X-API-Key` header, or returns `401`:

```bash
curl "http://localhost:8000/satellites/5/position" \
  -H "X-API-Key: dev-key-1"
```

Leave `KESSLER_API_KEYS` unset for local development. See `.env.example`.

## Development

```bash
pip install -e ".[dev]"                 # setup
pytest                                  # tests
ruff check . && ruff format --check .   # lint
uvicorn kessler.api:app --reload        # run locally
python -m kessler.ingest                # fetch TLE catalog into SQLite (WIP)
```
