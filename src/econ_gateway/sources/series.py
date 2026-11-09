"""One entry point over BLS and FRED: ``get_series``, and ``infer_source`` to route an ID.

The gateway's services use ``services/upstream.py`` (guarded sessions, release-aware cache).
``get_series`` is the plain path, used by the eval oracles and scripts.

Origin: Project 1 (``labor_agent.data.__init__``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Literal

import pandas as pd

from econ_gateway.sources.bls import BLSClient
from econ_gateway.sources.catalog import load_catalog
from econ_gateway.sources.fred import FREDClient

SourceChoice = Literal["auto", "BLS", "FRED"]
STANDARD_COLUMNS = ["series_id", "date", "value", "source"]

# Survey prefixes of BLS series IDs (CES, CPI, CPS, JOLTS, ECI, QCEW, LAUS, SAE, OEWS, PPI...).
_BLS_PREFIX = re.compile(
    r"^(CE[SU]|CU[SU]R|CW[SU]R|LN[SU]|JT[SU]|CI[SU]|CC[SU]|EN[SU]|LA[SU]|SM[SU]|OE[SU]|WP[SU]"
    r"|PR[SU]|AP[SU])"
)


def infer_source(series_id: str, catalog: pd.DataFrame | None = None) -> str:
    """``"BLS"`` or ``"FRED"``: the catalog's answer if listed, else BLS ID-prefix rules."""
    sid = series_id.strip().upper()
    try:
        cat = catalog if catalog is not None else load_catalog()
        hit = cat.loc[cat["series_id"] == sid, "source"]
        if not hit.empty:
            return str(hit.iloc[0]).upper()
    except FileNotFoundError:
        pass
    return "BLS" if _BLS_PREFIX.match(sid) and len(sid) >= 11 else "FRED"


def get_series(
    series_ids: str | Iterable[str],
    start: int | str = 2015,
    end: int | str | None = None,
    *,
    source: SourceChoice = "auto",
    include_annual: bool = False,
) -> pd.DataFrame:
    """Fetch series from BLS and/or FRED in one long format.

    Args:
        series_ids: One ID or several.
        start: Year (``2019``) or ISO date (``"2019-06-01"``).
        end: Year or ISO date; default is the current year.
        source: Force a source, or ``auto`` to infer per ID.
        include_annual: Keep BLS annual-average rows (``freq == "A"``).

    Returns:
        Columns ``series_id``, ``date``, ``value``, ``source`` (plus ``freq``, ``period``,
        ``footnotes`` for BLS rows), sorted by series and date.
    """
    ids = [series_ids] if isinstance(series_ids, str) else list(series_ids)
    start_s, end_s = str(start), (str(end) if end is not None else None)
    start_year = int(start_s[:4])
    end_year = int(end_s[:4]) if end_s else date.today().year
    try:
        catalog: pd.DataFrame | None = load_catalog()
    except FileNotFoundError:
        catalog = None

    by_source: dict[str, list[str]] = {"BLS": [], "FRED": []}
    for sid in ids:
        chosen = source if source != "auto" else infer_source(sid, catalog)
        by_source[chosen.upper()].append(sid.strip().upper())

    frames: list[pd.DataFrame] = []
    if by_source["BLS"]:
        bls = BLSClient().fetch(
            by_source["BLS"], start_year, end_year, annual_average=include_annual
        )
        frames.append(bls if include_annual else bls.loc[bls["freq"] != "A"])
    if by_source["FRED"]:
        fred = FREDClient()
        start_date = start_s if len(start_s) > 4 else f"{start_year}-01-01"
        end_date = end_s if end_s and len(end_s) > 4 else f"{end_year}-12-31"
        frames += [fred.observations(sid, start_date, end_date) for sid in by_source["FRED"]]

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    if len(start_s) > 4:  # trim whole-year BLS fetches to the exact dates asked for
        out = out.loc[out["date"] >= pd.Timestamp(start_s)]
    if end_s and len(end_s) > 4:
        out = out.loc[out["date"] <= pd.Timestamp(end_s)]
    return out.sort_values(["series_id", "date"]).reset_index(drop=True)
