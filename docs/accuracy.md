# Accuracy notes

Plain-English explanation of what kessler's numbers mean, and what they don't.

## TLEs and SGP4

A **TLE** (two-line element set) is a compact description of a satellite's
orbit at a specific moment, called its **epoch**. kessler ingests TLEs from
Celestrak's public GP data and propagates them with **SGP4**, the standard
analytic model used across the industry for this exact data format.

SGP4 is fast but approximate. Near the TLE's epoch, propagated positions are
typically accurate to roughly **1 km**. That error grows the further you
propagate from the epoch — after a few days it can reach many kilometers,
and it keeps growing the older the TLE gets. Two effects compound this:

- **Model error**: SGP4 itself is an approximation of orbital mechanics
  (atmospheric drag, Earth's oblateness, etc.), not an exact simulation.
- **Epoch age**: operators publish new TLEs periodically, so the further you
  are from a satellite's most recent TLE, the less that TLE reflects its
  actual current orbit.

## `epoch_age_hours` and `stale`

Every kessler response that involves propagation reports `epoch_age_hours`:
the number of hours between the TLE's epoch and the time being queried
(`at` for `/satellites/{id}/position`, or "now" for conjunction screening).
Responses also set `stale: true` once that age exceeds **72 hours**, as a
signal that accuracy is likely materially degraded.

There is no single "correct" staleness cutoff — 72 hours is a reasonable
default for LEO catalog objects, not a hard accuracy guarantee. Treat
`epoch_age_hours` as the number to actually reason about, and `stale` as a
convenience flag on top of it.

## Why miss distance is not collision probability

`/conjunctions/{norad_id}` reports **geometric miss distance only**: the
closest distance between two propagated trajectories during the screening
window, based on SGP4 output alone.

This is deliberately **not** a collision probability, and kessler does not
compute one in the MVP. A real probability of collision (Pc) estimate needs
**covariance** — the uncertainty in each object's position, which grows with
epoch age and varies by object and data source — combined with the miss
geometry. Public GP/TLE data does not include covariance, so any Pc computed
from it alone would imply a level of confidence the underlying data can't
support.

In short: a small `miss_distance_km` means the two propagated orbits pass
close together given the input TLEs, not that a collision is likely or
unlikely in any statistical sense. Always check `target_epoch_age_hours` and
`other_epoch_age_hours` on a result before acting on it, and treat
close-and-stale results as lower-confidence than close-and-fresh ones.
