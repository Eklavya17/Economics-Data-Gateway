"""Place resolution (``find_area``) and the ``area_id`` translation used by other tools.

BLS programs code the same place differently (DESIGN.md decision 13):

=============  ========  ==========  ==============================
Place          OEWS      QCEW        LAUS series stem
=============  ========  ==========  ==============================
United States  ``99``    ``US000``   CPS national series (LNS...)
Texas          ``48``    ``48000``   ``LASST48`` + 11 zeros
Austin metro   ``12420`` ``C1242``   ``LAUMT48`` + ``12420`` + 6 zeros
Travis County  —         ``48453``   ``LAUCN48453`` + 8 zeros
=============  ========  ==========  ==============================

The gateway exposes one readable ID per place (``us``, ``state:48``, ``metro:12420``,
``county:48453``) and translates it here. LAUS series IDs are the stem plus a two-digit measure
code (03 rate, 04 unemployed, 05 employed, 06 labor force); formats were verified live. Metro
and county LAUS data are published not seasonally adjusted only.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from econ_gateway.models import AreaLevel, AreaMatch, FailedItem, FindAreaResponse, LausSeries
from econ_gateway.resolve.entities import match_areas
from econ_gateway.services.upstream import Upstream

LAUS_MEASURES = {
    "unemployment_rate": "03",
    "unemployed": "04",
    "employed": "05",
    "labor_force": "06",
}
NATIONAL_LAUS = LausSeries(
    unemployment_rate="LNS14000000",  # CPS, the national counterpart of LAUS
    unemployed="LNS13000000",
    employed="LNS12000000",
    labor_force="LNS11000000",
    seasonal_adjustment="SA",
)
AMBIGUITY_RATIO = 0.9  # runner-up within 10% of the best score → tell the model to choose
# A bare city name ("Austin") almost always means the metro, but the area matcher scored
# "Austin County, Texas" 3.23 vs the metro's 3.16 (live). A small prior fixes the tie without
# beating an explicit "Austin County" query, which earns a +2 phrase-match bonus.
METRO_PRIOR = 0.3  # score points
_CANDIDATE_POOL = 3  # fetch limit x this many matches before re-ranking with the prior
_LAUS_STATES_MAX_FIPS = 56  # LAUS covers the 50 states + DC (FIPS ≤ 56) and Puerto Rico (72)
_LAUS_EXTRA_STATES = {"72"}

# Cache of the joined area table, keyed by the identity of the (process-cached) source tables.
_TABLE_CACHE: dict[tuple[int, int], pd.DataFrame] = {}


@dataclass(frozen=True)
class AreaRecord:
    """One place with its code in each BLS program.

    Attributes:
        area_id: Gateway ID (``us``, ``state:48``, ``metro:12420``, ``county:48453``).
        name: Official name.
        level: Geographic level.
        oews_code: OEWS area code, or ``None`` (counties are not published by OEWS).
        qcew_code: QCEW area code, or ``None``.
        laus_stem: LAUS series ID without the measure code, or ``None``.
    """

    area_id: str
    name: str
    level: AreaLevel
    oews_code: str | None
    qcew_code: str | None
    laus_stem: str | None

    def laus(self) -> LausSeries | None:
        """LAUS series IDs for this area (national CPS series for the U.S.)."""
        if self.level == "national":
            return NATIONAL_LAUS
        if self.laus_stem is None:
            return None
        ids = {name: f"{self.laus_stem}{code}" for name, code in LAUS_MEASURES.items()}
        return LausSeries(**ids, seasonal_adjustment="SA" if self.level == "state" else "NSA")


def _has_laus_state(fips: str) -> bool:
    return fips in _LAUS_EXTRA_STATES or (fips.isdigit() and int(fips) <= _LAUS_STATES_MAX_FIPS)


def build_area_table(oews_areas: pd.DataFrame, qcew_areas: pd.DataFrame) -> pd.DataFrame:
    """Join OEWS and QCEW area tables into one row per place (see module docstring).

    Args:
        oews_areas: Columns ``area``, ``area_title``, ``prim_state``, ``level``.
        qcew_areas: Columns ``area_fips``, ``area_title``.

    Returns:
        Columns ``area_id``, ``area_title``, ``level``, ``oews_code``, ``qcew_code``,
        ``laus_stem`` (``area_title`` / ``level`` names match ``resolve.entities.match_areas``).
    """
    qcew_codes = set(qcew_areas["area_fips"])
    states = oews_areas.loc[oews_areas["level"] == "state"]
    fips_by_abbr = dict(zip(states["prim_state"], states["area"], strict=True))
    rows: list[dict[str, str | None]] = [
        {
            "area_id": "us",
            "area_title": "United States",
            "level": "national",
            "oews_code": "99",
            "qcew_code": "US000",
            "laus_stem": None,
        }
    ]
    for _, state in states.iterrows():
        fips = str(state["area"]).zfill(2)
        rows.append(
            {
                "area_id": f"state:{fips}",
                "area_title": str(state["area_title"]),
                "level": "state",
                "oews_code": str(state["area"]),
                "qcew_code": f"{fips}000" if f"{fips}000" in qcew_codes else None,
                "laus_stem": f"LASST{fips}{'0' * 11}" if _has_laus_state(fips) else None,
            }
        )
    metros = oews_areas.loc[(oews_areas["level"] == "metro") & oews_areas["area"].str.len().eq(5)]
    for _, metro in metros.iterrows():
        cbsa = str(metro["area"])
        state_fips = fips_by_abbr.get(str(metro["prim_state"]))
        rows.append(
            {
                "area_id": f"metro:{cbsa}",
                "area_title": str(metro["area_title"]),
                "level": "metro",
                "oews_code": cbsa,
                "qcew_code": f"C{cbsa[:4]}" if f"C{cbsa[:4]}" in qcew_codes else None,
                # LAUS codes a multi-state metro under its primary state.
                "laus_stem": f"LAUMT{state_fips}{cbsa}{'0' * 6}" if state_fips else None,
            }
        )
    counties = qcew_areas.loc[
        qcew_areas["area_fips"].str.fullmatch(r"\d{5}")
        & ~qcew_areas["area_fips"].str.endswith("000")
        & ~qcew_areas["area_fips"].str.endswith("999")  # "Unknown or undefined" buckets
    ]
    for _, county in counties.iterrows():
        fips = str(county["area_fips"])
        rows.append(
            {
                "area_id": f"county:{fips}",
                "area_title": str(county["area_title"]),
                "level": "county",
                "oews_code": None,
                "qcew_code": fips,
                "laus_stem": f"LAUCN{fips}{'0' * 8}" if _has_laus_state(fips[:2]) else None,
            }
        )
    # QCEW's reference table repeats ~4,600 codes (seen live); keep one row per place.
    return pd.DataFrame(rows).drop_duplicates(subset="area_id").reset_index(drop=True)


def area_table(upstream: Upstream) -> pd.DataFrame:
    """The joined area table, built once per pair of source tables."""
    oews_areas, qcew_areas = upstream.oews_areas(), upstream.qcew_areas()
    key = (id(oews_areas), id(qcew_areas))
    if key not in _TABLE_CACHE:
        _TABLE_CACHE[key] = build_area_table(oews_areas, qcew_areas)
    return _TABLE_CACHE[key]


def _record(row: dict[str, str]) -> AreaRecord:
    return AreaRecord(
        area_id=row["area_id"],
        name=row["area_title"],
        level=row["level"],  # type: ignore[arg-type]  # values come from build_area_table
        oews_code=row["oews_code"] or None,
        qcew_code=row["qcew_code"] or None,
        laus_stem=row["laus_stem"] or None,
    )


def _rows(frame: pd.DataFrame) -> list[dict[str, str]]:
    """Rows as string dicts; missing values become ""."""
    return [
        {str(k): "" if pd.isna(v) else str(v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def find_area(
    query: str, upstream: Upstream, *, level: AreaLevel | None = None, limit: int
) -> FindAreaResponse:
    """Places matching a name, with their IDs in every program.

    Args:
        query: Place name or state abbreviation ("Texas", "TX", "Austin", "Travis County").
        upstream: Data access.
        level: Restrict to one geographic level.
        limit: Maximum candidates.

    Returns:
        Candidates best first, plus a note when the top matches are too close to call.
    """
    table = area_table(upstream)
    matched = match_areas(query, level=level, k=limit * _CANDIDATE_POOL, areas=table)
    matched = (
        matched.assign(score=matched["score"] + (matched["level"] == "metro") * METRO_PRIOR)
        .sort_values(by="score", ascending=False)
        .head(limit)
    )
    results: list[AreaMatch] = []
    for row in _rows(matched):
        record = _record(row)
        results.append(
            AreaMatch(
                area_id=record.area_id,
                name=record.name,
                level=record.level,
                has_occupation_wages=record.oews_code is not None,
                has_area_employment=record.qcew_code is not None,
                laus=record.laus(),
                score=round(float(row["score"]), 2),
            )
        )
    notes: list[str] = []
    if len(results) > 1 and results[1].score >= AMBIGUITY_RATIO * results[0].score:
        notes.append(
            "Several places match closely; check name and level before using an area_id, or "
            "ask the user which one they mean."
        )
    if not results:
        notes.append("No match. Use the official place name, e.g. 'Austin', 'Cook County'.")
    return FindAreaResponse(query=query, results=results, notes=notes)


def resolve_area_ids(
    area_ids: list[str], upstream: Upstream
) -> tuple[list[AreaRecord], list[FailedItem]]:
    """Translate gateway area IDs into program codes, keeping the caller's order.

    Returns:
        ``(records found, failures)``. Unknown IDs fail with a pointer to ``find_area``.
    """
    table = area_table(upstream).set_index("area_id", drop=False)
    found: list[AreaRecord] = []
    failed: list[FailedItem] = []
    for area_id in dict.fromkeys(a.strip().lower() for a in area_ids):
        if area_id not in table.index:
            failed.append(
                FailedItem(
                    id=area_id,
                    reason=f"Unknown area_id '{area_id}'.",
                    hint="Call find_area with the place name to get a valid area_id.",
                )
            )
            continue
        found.append(_record(_rows(table.loc[[area_id]])[0]))
    return found, failed
