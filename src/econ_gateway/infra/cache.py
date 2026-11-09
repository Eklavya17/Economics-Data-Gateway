"""Disk cache for observations whose freshness follows the agencies' release calendar.

The plain ``DiskCache`` expires every entry after a fixed TTL (12 h by default). Economic data
only changes when an agency publishes a release, so a fixed TTL is wrong in both directions:
it refetches unchanged CPI data twice a day for a month, and it can serve last month's jobs
numbers for up to 12 h after a new Employment Situation release. ``ReleaseAwareCache`` asks a
freshness check instead (``services/release_calendar.py``): an entry stays valid until the next
scheduled release of its series.

It subclasses ``sources.cache.DiskCache``, so the BLS and FRED clients use it unchanged (they
take a ``cache`` argument) and the on-disk layout stays the same.

**Stale mode** (``stale_ok=True``) ignores freshness entirely. The upstream layer uses it when
BLS or FRED cannot be reached: the last copy fetched beats no answer, provided the response
says how old it is. ``served`` records the fetch time of every entry handed out.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from econ_gateway.sources.cache import DiskCache

log = logging.getLogger(__name__)

# (namespace, series_id, fetched_at epoch seconds) -> is the entry still current?
FreshnessCheck = Callable[[str, str, float], bool]


class ReleaseAwareCache(DiskCache):
    """``DiskCache`` whose per-series entries expire on the release calendar.

    Args:
        root: Cache directory (``CACHE_DIR``).
        ttl_hours: Fallback TTL, used when ``is_fresh`` is ``None`` or fails.
        is_fresh: Freshness check for entries keyed by ``series_id``.
        stale_ok: Serve any entry regardless of age (outage fallback).
    """

    def __init__(
        self,
        root: Path,
        ttl_hours: float,
        *,
        is_fresh: FreshnessCheck | None = None,
        stale_ok: bool = False,
    ):
        super().__init__(root, ttl_hours)
        self._is_fresh = is_fresh
        self._stale_ok = stale_ok
        self.served: dict[str, float] = {}

    def get(
        self, namespace: str, parts: dict[str, Any], ttl_hours: float | None = None
    ) -> pd.DataFrame | None:
        """Return the cached frame if it is current (or if serving stale data), else ``None``.

        An explicit ``ttl_hours`` from the caller wins: the QCEW client passes one for files
        whose freshness is not tied to a series release.
        """
        series_id = parts.get("series_id")
        if ttl_hours is not None or not isinstance(series_id, str):
            return super().get(namespace, parts, ttl_hours)
        fetched_at = self.fetched_at(namespace, parts)
        if fetched_at is None:
            return None
        if self._stale_ok:
            frame = super().get(namespace, parts, ttl_hours=math.inf)
            if frame is not None:
                self.served[series_id] = fetched_at
            return frame
        if not self._fresh(namespace, series_id, fetched_at):
            return None
        return super().get(namespace, parts, ttl_hours=math.inf)

    def _fresh(self, namespace: str, series_id: str, fetched_at: float) -> bool:
        """Ask the freshness check; fall back to the fixed TTL if there is none or it fails."""
        if self._is_fresh is not None:
            try:
                return self._is_fresh(namespace, series_id, fetched_at)
            except Exception as exc:  # freshness is advisory: never fail a data request over it
                log.warning(
                    "freshness check failed; using fixed TTL",
                    extra={"series_id": series_id, "err": str(exc)},
                )
        return time.time() - fetched_at < self.ttl_hours * 3600
