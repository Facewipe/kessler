"""A small in-process, size-bounded TTL cache.

kessler runs as a single machine (see fly.toml), so a per-process cache is
sufficient -- no need for a distributed cache dependency for this.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class TTLCache[K, V]:
    """A `dict`-like cache where entries expire after `ttl_seconds` and the
    least-recently-used entry is evicted once `max_entries` is exceeded, so
    memory stays bounded regardless of how many distinct keys are requested.
    """

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: K, value: V) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self._ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
