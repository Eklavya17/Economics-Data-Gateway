"""Settings for the data-source clients (keys, cache locations), read from the environment.

The variable names are the ones Project 1 used (``BLS_API_KEY``, ``CACHE_DIR``...), so an
existing ``.env`` keeps working. Settings are read fresh on every call: it is cheap, and lets
tests change the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from econ_gateway.config import PROJECT_ROOT

# Curated catalog and reference tables, shipped inside the package (DESIGN.md decision 54).
REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
# Caches and downloads; outside the package because they are written at run time.
DATA_DIR = PROJECT_ROOT / "data"
# bls.gov rejects anonymous scripts for bulk files; set BLS_USER_AGENT to identify yourself.
DEFAULT_USER_AGENT = "econ-gateway/1.0 (python-requests)"


def _env(name: str, default: str | None = None) -> str | None:
    """A trimmed environment value, or ``default`` when unset or blank."""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True)
class SourceSettings:
    """Configuration of the BLS, FRED, OEWS and QCEW clients.

    Attributes:
        bls_api_key: BLS registration key (raises limits from 25 to 500 queries/day).
        fred_api_key: FRED API key (required for any FRED request).
        bls_user_agent: ``User-Agent`` header for every request.
        cache_ttl_hours: Fallback cache lifetime for entries with no other freshness rule.
        cache_dir: Parquet cache of API responses and parsed OEWS tables.
        raw_dir: Downloaded OEWS zip files.
        catalog_path: The curated series catalog CSV.
    """

    bls_api_key: str | None
    fred_api_key: str | None
    bls_user_agent: str
    cache_ttl_hours: float
    cache_dir: Path
    raw_dir: Path
    catalog_path: Path


def get_source_settings() -> SourceSettings:
    """Current settings from the environment (``.env`` is loaded when the package imports)."""
    return SourceSettings(
        bls_api_key=_env("BLS_API_KEY"),
        fred_api_key=_env("FRED_API_KEY"),
        bls_user_agent=_env("BLS_USER_AGENT") or DEFAULT_USER_AGENT,
        cache_ttl_hours=float(_env("CACHE_TTL_HOURS") or "12"),
        cache_dir=Path(_env("CACHE_DIR") or DATA_DIR / "cache"),
        raw_dir=Path(_env("RAW_DIR") or DATA_DIR / "raw"),
        catalog_path=Path(_env("CATALOG_PATH") or REFERENCE_DIR / "series_catalog.csv"),
    )
