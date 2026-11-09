"""The curated series catalog and the OEWS/QCEW reference tables.

The CSVs live in ``sources/reference/`` and ship with the package. They were built and validated
by Project 1's ``scripts/build_catalog.py`` (commit bdfb586): 296 series, each checked against the
live API. ``CATALOG_PATH`` can point at a different catalog.

Origin: Project 1 (``labor_agent.data.catalog``), now maintained here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from econ_gateway.sources.settings import REFERENCE_DIR, get_source_settings

REQUIRED_COLUMNS = [
    "series_id", "source", "title", "category", "frequency", "units", "seasonal_adjustment",
    "keywords",
]  # fmt: skip
FACET_COLUMNS = ["measure", "industry", "geography", "demographic", "validated", "last_obs"]


def load_catalog(path: Path | None = None) -> pd.DataFrame:
    """The catalog as strings, one row per series (a copy; safe to modify).

    Raises:
        FileNotFoundError: When the catalog file is missing.
        ValueError: When required columns are missing or IDs are duplicated.
    """
    path = Path(path or get_source_settings().catalog_path)
    return _load_catalog_cached(str(path), path.stat().st_mtime).copy()


@lru_cache(maxsize=4)
def _load_catalog_cached(path: str, _mtime: float) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str).fillna("")
    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Catalog {path} missing columns: {sorted(missing)}")
    for col in FACET_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame["series_id"] = frame["series_id"].str.strip().str.upper()
    dupes = pd.Series(frame.loc[frame["series_id"].duplicated(), "series_id"])
    if not dupes.empty:
        raise ValueError(f"Duplicate series IDs in catalog: {dupes.tolist()}")
    return frame


@lru_cache(maxsize=1)
def load_occupations() -> pd.DataFrame:
    """SOC occupations in OEWS: ``occ_code``, ``occ_title``, ``o_group``."""
    return pd.read_csv(REFERENCE_DIR / "oews_occupations.csv", dtype=str).fillna("")


@lru_cache(maxsize=1)
def load_oews_areas() -> pd.DataFrame:
    """OEWS areas: ``area``, ``area_title``, ``prim_state``, ``level``."""
    return pd.read_csv(REFERENCE_DIR / "oews_areas.csv", dtype=str).fillna("")


@lru_cache(maxsize=1)
def load_qcew_areas() -> pd.DataFrame:
    """QCEW areas: ``area_fips``, ``area_title``."""
    return pd.read_csv(REFERENCE_DIR / "qcew_areas.csv", dtype=str).fillna("")
