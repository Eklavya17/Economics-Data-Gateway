"""QCEW (Quarterly Census of Employment and Wages) open-data CSV API.

Docs: https://www.bls.gov/cew/additional-resources/open-data/ . No key needed; the API serves
roughly the five most recent years.

Area codes:     ``US000`` (U.S.), 5-digit county FIPS (``48453``), state ``48000``,
                MSA ``C1242`` (Austin); see bls.gov/cew/classifications/areas/
Industry codes: ``10`` (all industries), NAICS (``31-33``, ``5112``, ``62``)...
Ownership:      0 total, 1 federal, 2 state, 3 local, 5 private

Origin: Project 1 (``labor_agent.data.qcew``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import io

import pandas as pd

from econ_gateway.sources.cache import DiskCache, default_cache
from econ_gateway.sources.http import DEFAULT_TIMEOUT, make_session

QCEW_BASE = "https://data.bls.gov/cew/data/api"
CODE_COLS = [
    "area_fips", "own_code", "industry_code", "agglvl_code", "size_code", "disclosure_code",
    "lq_disclosure_code", "oty_disclosure_code",
]  # fmt: skip
QCEW_TTL_HOURS = 24 * 30  # a published quarter is revised rarely


class QCEWError(RuntimeError):
    """No QCEW file at that URL (quarter not published, or unknown area/industry code)."""


def parse_qcew_csv(text: str) -> pd.DataFrame:
    """Parse a QCEW CSV, keeping codes as strings (``"010"`` must not become 10)."""
    frame = pd.read_csv(io.StringIO(text), dtype=dict.fromkeys(CODE_COLS, str), low_memory=False)
    frame.columns = [c.strip().lower() for c in frame.columns]
    return frame


def fetch_qcew(
    year: int,
    quarter: int | str,
    *,
    area: str | None = None,
    industry: str | None = None,
    cache: DiskCache | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """One QCEW slice: all industries for an ``area``, or all areas for an ``industry``.

    Args:
        year: Reference year.
        quarter: 1-4, or ``"a"`` for annual averages.
        area: QCEW area code.
        industry: Industry code (pass exactly one of ``area`` / ``industry``).
        cache: Disk cache; defaults to ``CACHE_DIR``.
        use_cache: Read and write the cache.

    Raises:
        ValueError: Invalid arguments.
        QCEWError: Nothing published at that URL.
    """
    if (area is None) == (industry is None):
        raise ValueError("Pass exactly one of area= or industry=")
    q = str(quarter).lower()
    if q not in {"1", "2", "3", "4", "a"}:
        raise ValueError("quarter must be 1-4 or 'a'")
    if area is not None:
        kind, code = "area", area
    else:
        kind, code = "industry", str(industry).replace("-", "_")  # the API writes 31-33 as 31_33
    url = f"{QCEW_BASE}/{year}/{q}/{kind}/{code}.csv"

    cache = cache if cache is not None else default_cache()
    parts = {"url": url}
    if use_cache:
        cached = cache.get("qcew", parts, ttl_hours=QCEW_TTL_HOURS)
        if cached is not None:
            return cached
    response = make_session().get(url, timeout=DEFAULT_TIMEOUT)
    if response.status_code == 404:
        raise QCEWError(f"No QCEW data at {url} (check code/year; API covers ~5 recent years)")
    response.raise_for_status()
    frame = parse_qcew_csv(response.text)
    if use_cache and not frame.empty:
        cache.put("qcew", parts, frame)
    return frame
