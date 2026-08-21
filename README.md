# kessler

**Live service: [kessler.fly.dev](https://kessler.fly.dev)**

A satellite tracking and conjunction screening service built on public orbital
data. It answers two questions: *what is above me right now*, and *what is
about to pass close to what*.

Everything the web pages show is also available as a documented JSON API.

---

## What it does

| | |
|---|---|
| **[Sky view](https://kessler.fly.dev/sky)** | Polar chart of every catalogued object currently above your horizon — zenith at the centre, horizon at the rim, colour-coded by orbit regime. The arc of pink dots across the southern sky is the geostationary belt. |
| **[World map](https://kessler.fly.dev/demo)** | Live ground positions plotted on a Natural Earth basemap. |
| **[Conjunction screening](https://kessler.fly.dev/conjunctions/25544/view)** | Propagates a target against the full catalogue and reports time of closest approach and miss distance for every predicted close pass. |
| **[API docs](https://kessler.fly.dev/docs)** | OpenAPI / Swagger reference for all endpoints. |

## Endpoints

```
GET /health                              service status, catalogue size, newest TLE age
GET /satellites/{norad_id}/position      geodetic position at a given instant
GET /satellites/positions                sampled positions across the catalogue
GET /overhead?lat=&lon=                  everything above an observer's horizon
GET /conjunctions/{norad_id}             close approaches: TCA + miss distance
```

Example — what is above London right now:

```bash
curl "https://kessler.fly.dev/overhead?lat=51.5&lon=-0.12&min_elevation_deg=10"
```

## Stack

Python 3.12 · FastAPI · SGP4 · SQLite · pytest · ruff · Docker · Fly.io

No frontend framework — the pages are vanilla JS and inline SVG, served as
static files with no build step.

## How it works

**Data.** Celestrak's public GP/TLE catalogue, ingested on startup and
refreshed every 12 hours in the background, stored in SQLite. Records are
upserted by NORAD ID and only replaced when the incoming element set has a
strictly newer epoch, so repeated ingests never duplicate or regress.

**Propagation.** SGP4. Parsed `Satrec` objects and derived orbit ranges are
cached and rebuilt only when the catalogue content actually changes — not per
request.

**Conjunction screening.** A naive pairwise screen over a 72-hour window is
tens of millions of position evaluations, so it runs in three stages: a coarse
apogee/perigee overlap filter prunes the catalogue, surviving pairs are
propagated on a 60-second grid to collect local minima, and each candidate is
then refined at 1-second resolution to locate the true time of closest
approach.

**Staying responsive.** Screening is CPU-bound and long enough to starve
everything else on a single shared core — a request that saturates the event
loop stops health checks being served, and the platform then declares the
machine dead. Screening and overhead calculations run in a thread pool off the
event loop, responses are TTL-cached, and every computation carries a time
budget: exceeding it returns a partial result flagged `truncated` rather than
hanging.

## Accuracy, honestly

TLE-based propagation is roughly kilometre-accurate near an element set's
epoch and degrades as it ages. Every response reports `epoch_age_hours` and
flags anything older than 72 hours as `stale`.

Conjunction results are **geometric miss distance only**. No covariance is
used, so this is explicitly *not* a collision probability and is not suitable
for operational collision avoidance. See
[the accuracy notes](https://kessler.fly.dev/docs/accuracy) for the full
explanation.

The catalogue currently covers active satellites. Debris, spent rocket bodies
and classified objects are not included, and nothing smaller than roughly
10 cm is publicly tracked at all.

Two things worth knowing about screening real data:

- Screening the ISS returns its own modules and docked vehicles as zero-distance
  "conjunctions" — the catalogue lists each module under a separate NORAD ID.
  Filtering them needs more than a fixed distance threshold, because two
  independently-fitted element sets of the same physical object diverge under
  propagation in proportion to the gap between their epochs.
- Land polygons touching the antimeridian will draw a line clear across an
  equirectangular map unless longitude wrapping is handled explicitly.

## Running locally

```bash
pip install -e ".[dev]"
python -m kessler.ingest              # fetch the catalogue into SQLite
uvicorn kessler.api:app --reload      # http://127.0.0.1:8000
```

```bash
pytest                                # test suite
ruff check .                          # lint
python scripts/build_map.py           # regenerate the Natural Earth basemap
```

Set `KESSLER_API_KEYS` (comma-separated) to require an `X-API-Key` header on
every endpoint except `/health`. Leave it unset for open access.

## Deployment

Containerised and deployed to Fly.io on a single machine with a persistent
volume for the SQLite catalogue. See [docs/deploy.md](docs/deploy.md).

## Data

Orbital data from [Celestrak](https://celestrak.org/). Basemap from
[Natural Earth](https://www.naturalearthdata.com/) (public domain).
