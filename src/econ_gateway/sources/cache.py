"""Tiny disk cache for DataFrames: one parquet file plus JSON metadata per request key.

Origin: Project 1 (``labor_agent.data.cache``, commit bdfb586), now maintained here. The key
format is unchanged, so caches written by Project 1 can be reused by copying the directory.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd

from econ_gateway.sources.settings import get_source_settings


class DiskCache:
    """Cache DataFrames on disk under ``root/<namespace>/<key>.parquet``.

    Args:
        root: Cache directory.
        ttl_hours: Entries older than this are treated as missing. ``math.inf`` never expires;
            ``0`` never reuses (always refetch).
    """

    def __init__(self, root: Path, ttl_hours: float = 12.0):
        self.root = Path(root)
        self.ttl_hours = ttl_hours

    @staticmethod
    def make_key(parts: dict[str, Any]) -> str:
        """Stable short hash of the request parameters."""
        blob = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:20]

    def paths(self, namespace: str, parts: dict[str, Any]) -> tuple[Path, Path]:
        """``(data_path, meta_path)`` for a request."""
        key = self.make_key(parts)
        folder = self.root / namespace
        return folder / f"{key}.parquet", folder / f"{key}.json"

    def fetched_at(self, namespace: str, parts: dict[str, Any]) -> float | None:
        """Epoch seconds when the entry was stored, or ``None`` if there is no readable entry."""
        _, meta_path = self.paths(namespace, parts)
        try:
            value = json.loads(meta_path.read_text()).get("fetched_at")
        except (OSError, json.JSONDecodeError):
            return None
        return float(value) if isinstance(value, int | float) else None

    def get(
        self, namespace: str, parts: dict[str, Any], ttl_hours: float | None = None
    ) -> pd.DataFrame | None:
        """The cached frame, or ``None`` when missing, unreadable or older than the TTL.

        Args:
            namespace: Cache sub-folder (``bls``, ``fred``, ``qcew``).
            parts: Request parameters that identify the entry.
            ttl_hours: Overrides the cache's default TTL for this lookup.
        """
        data_path, _ = self.paths(namespace, parts)
        fetched_at = self.fetched_at(namespace, parts)
        if fetched_at is None or not data_path.exists():
            return None
        ttl = self.ttl_hours if ttl_hours is None else ttl_hours
        age_hours = (time.time() - fetched_at) / 3600
        if not math.isinf(ttl) and age_hours >= ttl:
            return None
        try:
            return pd.read_parquet(data_path)
        except Exception:  # a half-written or corrupt file is just a cache miss
            return None

    def put(self, namespace: str, parts: dict[str, Any], frame: pd.DataFrame) -> None:
        """Store ``frame`` for a request."""
        data_path, meta_path = self.paths(namespace, parts)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(data_path, index=False)
        meta = {"key_parts": parts, "fetched_at": time.time(), "rows": len(frame)}
        meta_path.write_text(json.dumps(meta, default=str))

    def clear(self, namespace: str | None = None) -> int:
        """Delete cached files (all, or one namespace); returns the number removed."""
        target = self.root / namespace if namespace else self.root
        removed = 0
        if target.exists():
            for path in target.rglob("*"):
                if path.is_file() and path.suffix in {".parquet", ".json"}:
                    path.unlink()
                    removed += 1
        return removed


def default_cache() -> DiskCache:
    """Cache at ``CACHE_DIR`` with ``CACHE_TTL_HOURS``."""
    settings = get_source_settings()
    return DiskCache(settings.cache_dir, settings.cache_ttl_hours)
