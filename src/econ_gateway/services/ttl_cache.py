"""Minimal in-process TTL cache for small values (metadata, availability answers)."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Key → value store whose entries expire after ``ttl_s`` seconds.

    Not thread-safe by design: entries are idempotent lookups, so a rare duplicate fetch when two
    worker threads miss at once is harmless and cheaper than locking.
    """

    def __init__(self, ttl_s: float):
        self._ttl_s = ttl_s
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` when missing or expired."""
        hit = self._items.get(key)
        if hit is None or time.monotonic() - hit[0] > self._ttl_s:
            return None
        return hit[1]

    def put(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""
        self._items[key] = (time.monotonic(), value)
