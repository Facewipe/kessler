"""Process-wide cache of parsed `Satrec`/`OrbitRange` pairs for the catalog.

Parsing every TLE into a `Satrec` and deriving its orbit range is identical
work on every `/conjunctions` request, regardless of which target is being
screened -- profiling showed it adding measurable, entirely avoidable cost on
top of the per-pair propagation itself. This cache rebuilds only when the
catalog's content has actually changed (a cheap (norad_id, epoch) signature
check against the freshly-read catalog), which in practice means once per
ingest cycle rather than once per request.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from kessler.db import SatelliteRecord
from kessler.propagate import satrec_from_tle
from kessler.screen import CachedSatellite, orbit_range

_lock = threading.Lock()
_signature: tuple[tuple[int, str | None], ...] | None = None
_cache: dict[int, CachedSatellite] = {}


def _signature_for(catalog: Sequence[SatelliteRecord]) -> tuple[tuple[int, str | None], ...]:
    return tuple(
        sorted(
            (record.norad_id, record.epoch_utc.isoformat() if record.epoch_utc else None)
            for record in catalog
        )
    )


def get_cached_catalog(catalog: Sequence[SatelliteRecord]) -> dict[int, CachedSatellite]:
    """Return `{norad_id: CachedSatellite}` for `catalog`.

    Rebuilds from scratch only when `catalog`'s (norad_id, epoch) signature
    differs from the last build -- i.e. when ingest has actually changed the
    stored catalog since -- and reuses the existing cache otherwise. A
    record whose TLE fails to parse is skipped (screening falls back to
    parsing it inline for that one record; see `kessler.screen._resolve`).

    Thread-safe: screening runs in a worker thread pool (see `kessler.api`),
    so concurrent requests may call this at the same time.
    """
    global _signature, _cache
    signature = _signature_for(catalog)
    with _lock:
        if signature != _signature:
            built: dict[int, CachedSatellite] = {}
            for record in catalog:
                try:
                    satrec = satrec_from_tle(record.line1, record.line2)
                except Exception:
                    continue
                built[record.norad_id] = CachedSatellite(
                    record=record, satrec=satrec, orbit_range=orbit_range(satrec)
                )
            _cache = built
            _signature = signature
        return _cache


def reset_cache() -> None:
    """Clear the cache. Exposed for tests that need a clean process-wide state."""
    global _signature, _cache
    with _lock:
        _signature = None
        _cache = {}
