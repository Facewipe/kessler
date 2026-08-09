# Accuracy notes

This page explains, in plain English, how accurate kessler's results are and
why. Read it before using the API to make operational decisions.

## What a TLE actually is

A **TLE** (two-line element set) is a compact description of a satellite's
orbit at one specific moment, called the **epoch**. It is not a live
position feed — it's a snapshot that a propagation model can extrapolate
forward or backward in time from.

kessler's MVP sources TLEs from [Celestrak](https://celestrak.org/)'s public
GP data, which is itself derived from US Space Force tracking. No account or
authentication is required to read this data, which is why it's the MVP
source; Space-Track (a lower-latency, more complete catalog that does
require an account) is planned for later.

## Why accuracy degrades with time

Positions are computed with **SGP4**, the standard analytical propagation
model for TLE data. SGP4 accounts for the dominant perturbations affecting
low Earth orbit satellites (atmospheric drag, Earth's oblateness, etc.), but
it is a simplified model, and the TLE itself is a fit to tracking data valid
only *near* its epoch.

The practical consequence: the further a query time is from the TLE's
epoch, the less trustworthy the result.

- **Near epoch** (minutes to a few hours): typically accurate to roughly a
  kilometer.
- **A day or two out**: error grows to several kilometers and keeps
  growing — how fast depends on the satellite's altitude, area-to-mass
  ratio, and solar activity (which affects atmospheric drag).
- **Old TLEs**: for a satellite that hasn't been re-tracked recently, error
  can reach tens of kilometers or more.

There is no simple formula for "error at time T" — it depends on the
specific object and space weather. Treat all positions as increasingly
approximate the further they are from epoch, and treat the age itself as
the best available accuracy signal.

## `epoch_age_hours` and `stale`

Every position response includes:

- `epoch_utc` — the TLE's epoch (when the underlying tracking data was
  fit).
- `epoch_age_hours` — how many hours separate the query time (`at`) from
  that epoch.
- `stale` — `true` once `epoch_age_hours` exceeds **72 hours**.

72 hours is a coarse, conservative cutoff, not a hard accuracy guarantee —
a TLE can be meaningfully degraded before 72 hours for some objects, and
still reasonably good just after it for others. Use `epoch_age_hours` as
the primary signal and `stale` as a convenient default threshold, not a
substitute for judgment.

## Why we report miss distance, not collision probability

kessler's conjunction screening (see the main README) reports **geometric
miss distance only**: the closest distance between two propagated orbits at
their time of closest approach (TCA), computed from TLE-derived positions.

We deliberately do **not** compute or report a collision probability
(`Pc`). A real probability of collision requires **covariance**
(uncertainty estimates for each object's position and velocity), which
public TLE data does not include. Any "probability" computed from TLEs
without covariance would be fabricated precision — it would look like a
rigorous number while actually resting on an assumption about uncertainty
that we do not have data to support.

If you need collision probability for operational decision-making, you
need a source that provides covariance (e.g. conjunction data messages from
Space-Track or a commercial provider), combined with a proper Pc
computation. kessler's MVP is a screening and triage tool: it helps you
find close approaches worth a closer look, not a substitute for an
operational conjunction assessment.
