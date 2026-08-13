# Deploying to Fly.io

This walks through deploying kessler to [Fly.io](https://fly.io) from
scratch. It assumes you've never used `flyctl` before and just runs the
commands in order.

## 0. Install flyctl

```bash
curl -L https://fly.io/install.sh | sh
```

(See the [Fly.io docs](https://fly.io/docs/flyctl/install/) for the
Windows/Homebrew installers if you'd rather use one of those.)

## 1. Log in

```bash
flyctl auth login
```

This opens a browser window to authenticate. If you don't have a Fly.io
account yet, it'll let you create one (a card is required once you go past
the free allowance, but the smallest VM + a few GB of volume storage fits
comfortably within it).

## 2. Launch the app

Run this from the repo root, where `Dockerfile` and `fly.toml` already live:

```bash
flyctl launch --no-deploy
```

`flyctl launch` detects the existing `fly.toml` and `Dockerfile` and asks a
few questions:

- **App name**: accept `kessler` (already set in `fly.toml`), or pick
  something else if that name is taken — Fly.io app names are globally
  unique. If you rename it, update the `app = "..."` line in `fly.toml` to
  match.
- **Region**: pick whichever is closest to you or your users. `fly.toml`
  defaults to `iad` (Ashburn, Virginia); change `primary_region` if you want
  somewhere else.
- **Postgres / Redis**: decline both — kessler uses SQLite on a volume, not
  a managed database.

`--no-deploy` stops it from deploying immediately, since the persistent
volume (next step) needs to exist first.

## 3. Create the persistent volume

The catalog lives in a SQLite file at `/data/kessler.db` inside the app, on
a Fly [volume](https://fly.io/docs/volumes/overview/) so it survives
restarts and redeploys. Create a small one (1 GB is overkill for the TLE
catalog, but it's the minimum size) in the **same region** you picked above:

```bash
flyctl volumes create kessler_data --region iad --size 1
```

Use the region you actually chose in step 2 if it wasn't `iad`. The volume
name (`kessler_data`) must match the `source` in `fly.toml`'s `[[mounts]]`
section — it already does, so you don't need to edit anything unless you
renamed it here too.

## 4. Deploy

```bash
flyctl deploy
```

This builds the Docker image, pushes it, and starts a machine with the
volume mounted at `/data`. On first boot the app finds an empty catalog and
automatically runs ingestion (see `kessler/api.py`'s startup hook), so
there's no manual seeding step — give it a minute, then check:

```bash
flyctl status
curl "https://kessler.fly.dev/health"
```

A healthy response looks like:

```json
{
  "status": "ok",
  "catalog_size": 8412,
  "newest_tle_epoch_utc": "2026-08-13T09:12:00+00:00",
  "newest_tle_epoch_age_hours": 3.1
}
```

If `catalog_size` stays `0` for more than a minute or two after deploy,
check the logs (next section) — the initial ingest may have hit a
Celestrak error.

## 5. Set secrets (optional)

If you want to require an API key (`KESSLER_API_KEYS`), set it as a Fly
secret rather than editing `fly.toml` — secrets are encrypted and never
committed to the repo:

```bash
flyctl secrets set KESSLER_API_KEYS="prod-key-1,prod-key-2"
```

Setting a secret triggers a redeploy automatically.

## Everyday commands

```bash
flyctl deploy          # ship a new version after code changes
flyctl logs             # tail application logs
flyctl status            # machine/health status
flyctl ssh console        # shell into the running machine
flyctl volumes list        # confirm the data volume is attached
```

## How data persistence works here

- `fly.toml` sets `KESSLER_DB_PATH=/data/kessler.db`, so the app reads and
  writes its SQLite catalog on the mounted volume instead of the
  container's ephemeral filesystem.
- On startup, if the catalog is empty (fresh volume, first deploy), the app
  automatically fetches and ingests the Celestrak catalog — no manual
  `python -m kessler.ingest` step needed.
- Every 12 hours after that, a background task re-runs ingestion to keep
  TLEs fresh, logging a one-line summary (`records fetched / inserted /
  updated / skipped`) you can see in `flyctl logs`.
- Because SQLite + a single volume don't support multiple machines writing
  concurrently, `fly.toml` pins `min_machines_running = 1` and
  `auto_stop_machines = false`. Don't scale this app horizontally without
  first moving off SQLite (see `CLAUDE.md`'s note that Postgres can replace
  it later).

## Troubleshooting

- **`flyctl deploy` fails to build**: run `docker build .` locally if you
  have Docker installed, to see the full build error without Fly's log
  truncation.
- **`/health` returns `catalog_size: 0` indefinitely**: check
  `flyctl logs` for `Catalog ingest (startup, empty catalog) failed` — this
  usually means Celestrak was unreachable or rate-limited at boot. The
  12-hour scheduled refresh will retry automatically, or trigger one
  immediately with `flyctl ssh console -C "python -m kessler.ingest"`.
- **Volume not found on deploy**: confirm `flyctl volumes list` shows
  `kessler_data` in the same region as your machine — volumes are
  region-specific and won't attach across regions.
