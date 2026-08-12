# kessler
Satellite conjunction screening API built on open orbital data (Celestrak),
propagated via SGP4. Geometric miss distance only — see
[`docs/accuracy.md`](docs/accuracy.md) for what that means and why it's not
a collision probability.

## Quickstart

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Seed a demo satellite into SQLite
# (python -m kessler.ingest, which fetches the live Celestrak catalog, is
# still a WIP stub — this seeds one pinned reference TLE so you have data
# to query right away. Its epoch is from 2000, so position queries will
# report `stale: true` unless you pass an `at` near that epoch — that's
# expected, and a good demonstration of the staleness flag itself.)
python -c "
from kessler.db import SatelliteRecord, get_connection, upsert_satellite

conn = get_connection('kessler.db')
upsert_satellite(conn, SatelliteRecord(
    norad_id=5,
    name='SGP4-VER TEST SATELLITE 5',
    line1='1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
    line2='2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667',
))
"

# 3. Run
uvicorn kessler.api:app --reload
```

Dev commands:

```bash
pytest                                  # tests
ruff check . && ruff format --check .   # lint
python -m kessler.ingest                # fetch TLE catalog into SQLite (WIP)
```

## TLE ingestion

`python -m kessler.ingest` downloads the active-satellites Celestrak GP
dataset in TLE format, parses it, and upserts records into a local SQLite
database keyed on `norad_id`. Records are only replaced when the freshly
fetched TLE has a strictly newer epoch than what is stored, so running the
command repeatedly never creates duplicate rows. Malformed records are
skipped with a logged warning instead of failing the run.

The database path is configurable via the `KESSLER_DB_PATH` environment
variable (default: `kessler.db` in the working directory).

## API

Interactive OpenAPI docs (with request/response examples) are served at
`/docs` once the app is running.

### Live demo map

`GET /demo` serves a self-contained HTML page (plain HTML/CSS/JS, no build
step, no external dependencies) that plots live positions for a curated set
of ~21 well-known satellites (ISS and others) on a 2D map, refreshed every
30 seconds. Clicking a satellite opens a side panel with its position
details and current conjunctions, pulled from the endpoints below. It's a
shop window for the API, not a product UI — satellites missing from your
locally ingested catalog are skipped silently.

```bash
open "http://localhost:8000/demo"
```

### Check service health

```bash
curl "http://localhost:8000/health"
```

```json
{"status": "ok"}
```

### Get a satellite's current position

```bash
# 5 is the demo satellite seeded in the quickstart above
curl "http://localhost:8000/satellites/5/position"
```

Optionally pass an ISO 8601 UTC timestamp via `at` (defaults to now):

```bash
curl "http://localhost:8000/satellites/5/position?at=2000-06-27T18:00:00Z"
```

Example response (illustrative — for a satellite with a fresher TLE than the
demo's pinned 2000-epoch one):

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
# 5 is the demo satellite seeded in the quickstart above; with only one
# satellite in the catalog this returns an empty "conjunctions" list — seed
# a second satellite to see a match.
curl "http://localhost:8000/conjunctions/5?hours=72&threshold_km=10"
```

`hours` (1-168, default 72) sets the screening window length from now;
`threshold_km` (1-50, default 10) sets both the coarse orbit-overlap buffer
and the candidate miss-distance bound; `min_separation_km` (0-50, default 1)
excludes pairs that never separate by more than this across the whole
window — e.g. a station's own modules or a docked vehicle, which are
physically the same cluster rather than a conjunction. This bound widens
automatically with the epoch-age gap between the pair's TLEs, since an older
TLE naturally drifts further from a fresher one under propagation even for a
physically co-located object.

Example response (illustrative, with a fuller catalog):

```json
{
  "disclaimer": "Geometric screening on public TLEs (SGP4), not a collision probability. No covariance is used; treat results as a geometric proximity estimate only.",
  "target_norad_id": 25544,
  "target_name": "ISS (ZARYA)",
  "window_start_utc": "2026-08-09T12:00:00+00:00",
  "window_end_utc": "2026-08-12T12:00:00+00:00",
  "threshold_km": 10.0,
  "min_separation_km": 1.0,
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
distance. Pairs that stay within `min_separation_km` (widened for the pair's
epoch-age gap) of each other for the entire window are excluded as
co-located. Results are sorted by miss distance, ascending. As with the
position endpoint, this reports **geometric miss distance only** — never a
collision probability.

Returns `404` for an unknown `norad_id` and `422` for `hours`,
`threshold_km`, or `min_separation_km` outside their allowed ranges.

### List satellites currently overhead

```bash
curl "http://localhost:8000/overhead?lat=51.5074&lon=-0.1278&min_elevation_deg=10"
```

`lat` (-90 to 90) and `lon` (-180 to 180) are required. `min_elevation_deg`
(0-90, default 10) sets the minimum elevation above the horizon to report;
`alt_m` (default 0) is the observer's altitude above the WGS84 ellipsoid, in
meters.

Example response:

```json
{
  "at": "2026-08-12T12:00:00+00:00",
  "observer": {"lat": 51.5074, "lon": -0.1278, "alt_m": 0.0},
  "min_elevation_deg": 10.0,
  "count": 1,
  "satellites": [
    {
      "norad_id": 25544,
      "name": "ISS (ZARYA)",
      "elevation_deg": 45.213,
      "azimuth_deg": 132.704,
      "range_km": 850.331,
      "alt_km": 420.123,
      "epoch_age_hours": 5.1,
      "stale": false
    }
  ]
}
```

Propagates the whole catalog to the current time. To keep this fast against
a large catalog, each satellite is first pruned by the great-circle ground
distance between the observer and its sub-satellite point — comparing that
against the maximum ground range at which a satellite at its altitude could
possibly clear `min_elevation_deg` — before the more expensive topocentric
(elevation/azimuth/range) conversion runs, which only happens for satellites
that survive the coarse filter. `azimuth_deg` is measured clockwise from
north. Results are sorted by elevation, descending.

Returns `422` for `lat`, `lon`, or `min_elevation_deg` outside their allowed
ranges.

## Authentication

By default the API is open (dev mode) — no key required. To require an API
key, set `KESSLER_API_KEYS` to a comma-separated list of accepted keys
before starting the app:

```bash
export KESSLER_API_KEYS="dev-key-1,dev-key-2"
uvicorn kessler.api:app --reload
```

Once set, every endpoint except `/health` requires an `X-API-Key` header
matching one of the configured keys:

```bash
curl "http://localhost:8000/satellites/5/position" -H "X-API-Key: dev-key-1"
```

Requests without a valid key get `401 Unauthorized`. Leaving
`KESSLER_API_KEYS` unset keeps the API fully open, which is the default for
local development.

## Python example

```python
import requests

BASE_URL = "http://localhost:8000"
# Only needed if KESSLER_API_KEYS is set on the server.
HEADERS = {"X-API-Key": "dev-key-1"}

response = requests.get(f"{BASE_URL}/satellites/5/position", headers=HEADERS)
response.raise_for_status()
position = response.json()

if position["stale"]:
    print(f"Warning: TLE is {position['epoch_age_hours']:.1f}h old, accuracy may be degraded")

print(
    f"{position['name']}: lat={position['lat']}, lon={position['lon']}, alt_km={position['alt_km']}"
)
```
