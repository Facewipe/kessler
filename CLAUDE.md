# CLAUDE.md

Working instructions for coding agents on this repository.

## What this project is

**kessler** is a satellite tracking and conjunction screening service built on
public orbital data. Live at [kessler.fly.dev](https://kessler.fly.dev).

It ingests Celestrak's public GP/TLE catalogue, propagates positions with
SGP4, and answers two questions: what is above a given observer right now,
and what is about to pass close to what. Everything the web pages show is
also served as a documented JSON API.

## Tech stack

- Python 3.12, FastAPI, uvicorn
- `sgp4` for propagation
- SQLite for storage — a single file on a mounted volume in production
- pytest, ruff
- Docker, deployed to Fly.io (single machine, persistent volume, London region)
- GitHub Actions: lint and tests on every push and PR; deploy on merge to main

Web pages are vanilla JavaScript and inline SVG served as static files. No
framework, no build step, no CDN. Keep it that way.

## Layout

```
kessler/
  api.py            FastAPI app and all HTTP endpoints
  ingest.py         Celestrak fetch, TLE parsing, upsert
  db.py             SQLite storage layer
  propagate.py      SGP4 wrapper, coordinate conversions
  screen.py         conjunction screening
  overhead.py       topocentric elevation/azimuth for an observer
  catalog_cache.py  parsed Satrec + orbit range cache
  ttl_cache.py      bounded time-to-live response cache
  static/           demo.html, sky.html, shared.css, world.json
tests/              pytest, fixtures in tests/fixtures/
scripts/build_map.py  regenerates world.json from Natural Earth
docs/               accuracy notes, deployment guide
```

## Commands

```bash
pip install -e ".[dev]"                 # setup
pytest                                  # tests
ruff check . && ruff format .           # lint and format
uvicorn kessler.api:app --reload        # run locally
python -m kessler.ingest                # fetch catalogue into SQLite
python scripts/build_map.py             # regenerate the basemap (needs network)
```

## How work happens here

- One issue, one branch, one pull request. Keep changes focused.
- **Never merge to main.** Only the repository owner merges.
- Every PR must pass CI, include tests for new behaviour, and update the docs
  when endpoints or behaviour change.
- If an issue is ambiguous, make a reasonable assumption, state it explicitly
  in the PR description, and continue — do not stall waiting for an answer.

### Before pushing

Always run `ruff check --fix .`, `ruff format .` and `pytest` and confirm they
pass. CI is the only thing standing between a change and production.

### Fixing existing work

To fix code already written for an issue, **comment on the pull request, not
the issue**. An issue comment starts a fresh empty branch and the work will
not find the existing code.

When fixing a CI failure, push to the same branch. Do not open a new one.

### Known limitation

Agents cannot create or modify files under `.github/workflows/` — GitHub
blocks it. If a task needs a workflow change, write the complete file content
into the PR description and let the owner add it manually.

## Domain notes

A **TLE** describes an orbit at a given **epoch**. Accuracy is roughly
kilometre-level near the epoch and degrades as the element set ages, so every
response reports `epoch_age_hours` and flags anything older than 72 hours as
`stale`.

**Conjunction screening** runs in three stages: a coarse apogee/perigee
overlap filter prunes the catalogue, surviving pairs are propagated on a
60-second grid to collect local minima, and each candidate is refined at
1-second resolution to locate the true time of closest approach.

Results are **geometric miss distance only**. No covariance is used, so this
is explicitly not a collision probability. Never present it as one, and keep
that caveat visible wherever results are shown.

Two things that are easy to get wrong on real data:

- Screening an object like the ISS returns its own modules and docked
  vehicles at near-zero distance, because the catalogue lists each under a
  separate NORAD ID. Filtering them needs more than a fixed threshold: two
  independently-fitted element sets of the same physical object diverge under
  propagation in proportion to the gap between their epochs.
- On an equirectangular map, +180° and -180° must not fold onto the same
  edge, or any coastline touching the antimeridian draws a line across the
  whole map.

## Performance constraints

The production machine has one shared CPU core. Screening and overhead
calculations are CPU-bound and long enough to starve everything else, which
stops health checks being served and makes the platform declare the machine
dead.

Therefore:

- Heavy computation runs in the shared thread pool, off the event loop.
- Parsed `Satrec` objects and orbit ranges come from `catalog_cache`, rebuilt
  only when the catalogue content changes — never per request.
- Responses are TTL-cached.
- Every computation carries a time budget. Exceeding it returns a partial
  result flagged `truncated`, never an unbounded wait.
- `/health` must stay cheap. It reads a cached snapshot and must never query
  the catalogue per request.

If you add an endpoint that does real work, it follows all of the above.

## Conventions

- All code, comments, commit messages and documentation in English.
- Type hints everywhere; docstrings on public functions.
- No network calls in tests. Use fixtures in `tests/fixtures/`.
- Secrets never in code or git. Local: `.env` (gitignored), with placeholders
  in the committed `.env.example`. CI and production: repository secrets and
  `flyctl secrets`.
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Prefer boring, readable solutions over clever ones.

## Writing tests

A test that only passes on a particular machine is worse than no test. Do not
assert wall-clock timings against production budgets — CI runners are slower
and the result becomes a measure of the runner, not of this code. Assert the
mechanism deterministically instead, and keep the measured numbers in a
comment as evidence.
