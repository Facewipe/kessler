"""Tests for kessler.ttl_cache.TTLCache."""

from __future__ import annotations

import time

from kessler.ttl_cache import TTLCache


def test_get_returns_none_for_missing_key() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60, max_entries=10)

    assert cache.get("missing") is None


def test_set_then_get_returns_the_value() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60, max_entries=10)

    cache.set("a", 1)

    assert cache.get("a") == 1


def test_entry_expires_after_ttl() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=0.01, max_entries=10)

    cache.set("a", 1)
    time.sleep(0.02)

    assert cache.get("a") is None


def test_max_entries_evicts_least_recently_used() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60, max_entries=2)

    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # over capacity: "a" (least recently touched) is evicted

    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_get_marks_entry_as_recently_used() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60, max_entries=2)

    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # touch "a" so "b" becomes the least recently used
    cache.set("c", 3)

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_clear_removes_all_entries() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60, max_entries=10)
    cache.set("a", 1)

    cache.clear()

    assert cache.get("a") is None
    assert len(cache) == 0
