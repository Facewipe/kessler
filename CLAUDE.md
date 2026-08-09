# CLAUDE.md

## What this project is

**kessler** is a satellite conjunction screening API built on open orbital data
(Celestrak for MVP, Space-Track later). Target users: developers, analysts and
small satellite operators who need conjunction data as an API rather than a
portal. Business model: hosted API with a free tier. The core stays
closed-source; a client SDK may be open-sourced later in a separate repo.

## How work happens here (important)

- The owner reviews in the evenings. Work is asynchronous via GitHub Issues.
- One issue = one branch = one pull request. Keep PRs small and focused.
- **Never merge to main.** Only the owner merges.
- Every PR must: pass CI, include tests for new behavior, update docs if
  endpoints or behavior change, and briefly explain decisions in the PR
  description.
- If an issue is ambiguous, make a reasonable assumption, state it clearly in
  the PR description, and proceed — do not stall.
- The owner may write issues or comments in Russian. Always reply in the same
  language the comment used, but keep all code, commits and docs in English.

## Tech stack

- Python 3.12, FastAPI + uvicorn
- `sgp4` for propagation (use `skyfield` only if it clearly simplifies code)
- SQLite for MVP storage (single file, zero ops); keep the data layer thin so
  Postgres can replace it later
- pytest for tests, ruff for lint + format
- GitHub Actions: lint + tests on every push and PR

## Conventions

- All code, comments, commit messages and docs in English.
- Type hints everywhere; docstrings on public functions.
- No network calls in tests. Use fixture files in `tests/fixtures/`.
- Secrets never go into code or git. Local dev: `.env` (gitignored) with a
  committed `.env.example` containing placeholders. CI: GitHub Secrets.
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Prefer boring, readable solutions over clever ones.

## Domain notes

- A **TLE** (two-line element set) describes an orbit at a given **epoch**.
  MVP source: Celestrak GP data (no auth required).
- Propagation uses **SGP4**. Accuracy is roughly km-level near epoch and
  degrades as the TLE ages. Therefore: always store the epoch, expose
  `epoch_age_hours` in API responses, and flag results based on TLEs older
  than 72 hours as stale.
- **Conjunction screening (MVP):** for a target satellite over a time window,
  apply a coarse apogee/perigee overlap filter to prune the catalog, then
  propagate candidate pairs on a coarse time grid and refine local minima to
  find **TCA** (time of closest approach) and **miss distance**.
- We report **geometric miss distance only**. No covariance, no collision
  probability in the MVP. Never present results as collision probability;
  state this limitation in the docs wherever results are explained.

## Definition of done

- `pytest` passes locally and in CI; `ruff check` and `ruff format --check`
  are clean.
- New behavior is covered by tests.
- README / docs updated when endpoints change.

## Commands

```bash
pip install -e ".[dev]"      # setup
pytest                        # tests
ruff check . && ruff format --check .   # lint
uvicorn kessler.api:app --reload        # run locally
python -m kessler.ingest      # fetch TLE catalog into SQLite
```

## Target layout

```
kessler/          # package: api.py, ingest.py, propagate.py, screen.py, db.py
tests/            # pytest, fixtures in tests/fixtures/
docs/             # accuracy notes, quickstart
.github/workflows/ci.yml
```
## Before pushing

Always apply `ruff check --fix .` and `ruff format .` before pushing, and
fix any failing tests. When fixing CI failures, push to the SAME branch —
never create a new one.
