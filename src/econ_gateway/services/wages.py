"""Occupation lookup and OEWS wages (``find_occupation``, ``get_occupation_wages``).

OEWS (Occupational Employment and Wage Statistics) estimates employment and wages for ~830
occupations in the nation, states and metro areas, for May of each year. Things the code
handles on purpose (DESIGN.md decision 14):

- The latest year is discovered by trying (last year first), never assumed.
- Only cross-industry rows are used (the national file also has per-industry rows).
- ``#`` cells in the source are wages at or above the reporting cap. They arrive as NaN with a
  ``<col>_capped`` flag: reported in ``top_coded``, never as a number, and ranked *first* when
  sorting by that measure (their true value is the highest).
- Counties are not published by OEWS; such requests fail with a pointer to the metro.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

import pandas as pd

from econ_gateway.consts import (
    OEWS_CURRENT_URL,
    OEWS_TABLES_URL,
    OEWS_YEARS_TO_TRY,
)
from econ_gateway.models import (
    FailedItem,
    FindOccupationResponse,
    OccupationMatch,
    OccupationWagesResponse,
    ResponseFormat,
    WageMeasure,
    WageRow,
)
from econ_gateway.resolve.entities import match_occupations
from econ_gateway.services.areas import AreaRecord, resolve_area_ids
from econ_gateway.services.errors import DataNotAvailableError, InvalidRequestError
from econ_gateway.services.upstream import Upstream
from econ_gateway.services.upstream_places import OewsLevel

RankScope = Literal["states", "metros"]
# Keep responses within the token budget (docs/RESEARCH.md §2): ~300 characters per row.
WAGES_MAX_ROWS = 120  # rows across all occupations in one response

# WageRow field -> OEWS column. Percentiles p25/p75 are only filled in the detailed format.
_FIELDS = {
    "employment": "tot_emp",
    "jobs_per_1000": "jobs_1000",
    "location_quotient": "loc_quotient",
    "hourly_mean": "h_mean",
    "hourly_median": "h_median",
    "annual_mean": "a_mean",
    "annual_median": "a_median",
    "annual_p10": "a_pct10",
    "annual_p25": "a_pct25",
    "annual_p75": "a_pct75",
    "annual_p90": "a_pct90",
}
_DETAILED_ONLY = {"annual_p25", "annual_p75"}
_LEVEL_FOR_SCOPE: dict[RankScope, OewsLevel] = {"states": "state", "metros": "metro"}


# ---------------------------------------------------------------- find_occupation


def find_occupation(query: str, upstream: Upstream, *, limit: int) -> FindOccupationResponse:
    """SOC occupations matching plain words ("nurse", "software developer", "15-1252").

    Uses ``resolve.entities``: aliases for common names, phrase and token overlap, fuzzy ratio.
    """
    matched = match_occupations(query, k=limit, occupations=upstream.occupations())
    results = [
        OccupationMatch(
            occupation_code=str(row["occ_code"]),
            title=str(row["occ_title"]),
            group=str(row["o_group"]),
            score=round(float(row["score"]), 2),
        )
        for row in matched.to_dict(orient="records")
    ]
    return FindOccupationResponse(query=query, results=results)


# ---------------------------------------------------------------- year discovery


def latest_oews_year(upstream: Upstream, today: date) -> int:
    """Newest OEWS year that BLS has published (tries last year, then earlier years).

    Raises:
        DataNotAvailableError: When none of the recent years can be loaded.
    """
    errors: list[str] = []
    for year in range(today.year - 1, today.year - 1 - OEWS_YEARS_TO_TRY, -1):
        try:
            upstream.oews(year, "national")
            return year
        except DataNotAvailableError as exc:
            errors.append(str(exc))
    raise DataNotAvailableError("No recent OEWS release could be loaded: " + " | ".join(errors))


# ---------------------------------------------------------------- rows


def _cross_industry(table: pd.DataFrame) -> pd.DataFrame:
    if "i_group" in table.columns:
        return table.loc[table["i_group"] == "cross-industry"]
    return table


def _value(row: dict[str, object], column: str) -> float | None:
    value = row.get(column)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)  # type: ignore[arg-type]  # numeric OEWS columns


def _wage_row(row: dict[str, object], area_id: str, area_name: str, *, detailed: bool) -> WageRow:
    """Map one OEWS record to a ``WageRow``."""
    values: dict[str, float | None] = {}
    top_coded: list[str] = []
    for field, column in _FIELDS.items():
        if field in _DETAILED_ONLY and not detailed:
            continue
        values[field] = _value(row, column)
        if row.get(f"{column}_capped") is True:
            top_coded.append(field)
    return WageRow(
        area_id=area_id,
        area_name=area_name,
        occupation_code=str(row["occ_code"]),
        occupation=str(row["occ_title"]),
        annual_only=row.get("annual") is True,
        top_coded=top_coded,
        **values,
    )


def _sort_key(row: WageRow, measure: WageMeasure) -> float:
    """Descending sort key; top-coded values are the largest, unpublished ones the smallest."""
    if measure in row.top_coded:
        return math.inf
    value = getattr(row, measure)
    return -math.inf if value is None else float(value)


def _area_id_for(level: OewsLevel, code: str) -> str:
    if level == "national":
        return "us"
    return f"state:{code.zfill(2)}" if level == "state" else f"metro:{code}"


def _rows_for(
    table: pd.DataFrame, occupation_codes: list[str], area_codes: list[str] | None
) -> list[dict[str, object]]:
    picked = table.loc[table["occ_code"].isin(occupation_codes)]
    if area_codes is not None:
        picked = picked.loc[picked["area"].isin(area_codes)]
    return [{str(k): v for k, v in r.items()} for r in picked.to_dict(orient="records")]


# ---------------------------------------------------------------- get_occupation_wages


def _validate_occupations(
    codes: list[str], upstream: Upstream
) -> tuple[list[str], list[FailedItem]]:
    known = set(upstream.occupations()["occ_code"])
    valid = [c for c in dict.fromkeys(codes) if c in known]
    failed = [
        FailedItem(
            id=c,
            reason=f"'{c}' is not an OEWS occupation code.",
            hint="Call find_occupation with the job title to get a valid SOC code.",
        )
        for c in dict.fromkeys(codes)
        if c not in known
    ]
    return valid, failed


def _oews_level(record: AreaRecord) -> OewsLevel | None:
    """OEWS table that holds this area, or ``None`` for counties (OEWS does not publish them)."""
    if record.oews_code is None:
        return None
    if record.level == "national":
        return "national"
    if record.level == "state":
        return "state"
    return "metro" if record.level == "metro" else None


def _explicit_area_rows(
    records: list[AreaRecord],
    occupations: list[str],
    tables: dict[OewsLevel, pd.DataFrame],
    *,
    detailed: bool,
) -> tuple[list[WageRow], list[FailedItem]]:
    rows: list[WageRow] = []
    failed: list[FailedItem] = []
    for record in records:
        level = _oews_level(record)
        if level is None or record.oews_code is None:
            failed.append(
                FailedItem(
                    id=record.area_id,
                    reason=f"OEWS does not publish wages for counties ({record.name}).",
                    hint="Call find_area with level='metro' for the metro area that contains it.",
                )
            )
            continue
        found = _rows_for(tables[level], occupations, [record.oews_code])
        seen = {str(r["occ_code"]) for r in found}
        rows += [_wage_row(r, record.area_id, record.name, detailed=detailed) for r in found]
        failed += [
            FailedItem(
                id=f"{occ}@{record.area_id}",
                reason=f"BLS published no estimate for {occ} in {record.name}.",
                hint="Estimates are suppressed when too few employers report; try the state "
                "or a larger metro, or a broader occupation group.",
            )
            for occ in occupations
            if occ not in seen
        ]
    return rows, failed


def _ranked_rows(
    scope: RankScope,
    occupations: list[str],
    tables: dict[OewsLevel, pd.DataFrame],
    *,
    sort_by: WageMeasure,
    limit: int,
    detailed: bool,
) -> list[WageRow]:
    level = _LEVEL_FOR_SCOPE[scope]
    rows: list[WageRow] = []
    for occ in occupations:
        candidates = [
            _wage_row(
                r, _area_id_for(level, str(r["area"])), str(r["area_title"]), detailed=detailed
            )
            for r in _rows_for(tables[level], [occ], None)
        ]
        candidates.sort(key=lambda row: _sort_key(row, sort_by), reverse=True)
        rows += candidates[:limit]
    return rows


def get_occupation_wages(
    occupation_codes: list[str],
    upstream: Upstream,
    *,
    area_ids: list[str] | None = None,
    rank_across: RankScope | None = None,
    sort_by: WageMeasure = "annual_median",
    limit: int,
    year: int | None = None,
    response_format: ResponseFormat = "concise",
    today: date | None = None,
) -> OccupationWagesResponse:
    """OEWS wages for occupations in specific areas, or ranked across all states/metros.

    Args:
        occupation_codes: SOC codes from ``find_occupation``.
        upstream: Data access.
        area_ids: Specific places (from ``find_area``). Mutually exclusive with ``rank_across``.
        rank_across: Rank every state or every metro by ``sort_by``.
        sort_by: Measure used for ranking.
        limit: Areas kept per occupation when ranking.
        year: OEWS May reference year; default is the latest published.
        response_format: ``detailed`` adds 25th/75th percentiles.
        today: Override for tests.

    Returns:
        Rows, national comparison rows, failures, reading notes and citation links.

    Raises:
        InvalidRequestError: When both ``area_ids`` and ``rank_across`` are given.
        DataNotAvailableError: When the requested (or any recent) OEWS year is not available.
    """
    if area_ids and rank_across:
        raise InvalidRequestError(
            "Pass either area_ids (specific places) or rank_across ('states' or 'metros'), "
            "not both. To compare a place with the ranking, make two calls."
        )
    today = today or date.today()
    detailed = response_format == "detailed"
    latest = latest_oews_year(upstream, today)
    year = year or latest
    occupations, failed = _validate_occupations(occupation_codes, upstream)
    records, area_failures = resolve_area_ids(area_ids or [], upstream)
    failed += area_failures

    needed: set[OewsLevel] = {"national"}
    needed |= {level for r in records if (level := _oews_level(r)) is not None}
    if rank_across:
        needed.add(_LEVEL_FOR_SCOPE[rank_across])
    tables: dict[OewsLevel, pd.DataFrame] = {
        level: _cross_industry(upstream.oews(year, level)) for level in needed
    }

    notes = [
        f"OEWS estimates are for May {year} (published spring {year + 1}) and are not seasonally "
        "adjusted. OEWS is a survey snapshot, not a time series; compare years with care.",
        "Annual wages for hourly-paid occupations are hourly wage x 2,080 hours. "
        "annual_only=true means BLS publishes only annual pay (e.g. teachers).",
    ]
    per_occupation = max(1, min(limit, WAGES_MAX_ROWS // max(len(occupations), 1)))
    if rank_across:
        rows = _ranked_rows(
            rank_across,
            occupations,
            tables,
            sort_by=sort_by,
            limit=per_occupation,
            detailed=detailed,
        )
        notes.append(
            f"Top {per_occupation} {rank_across} per occupation by {sort_by}. Values marked "
            f"top_coded are at or above the OEWS cap and rank first; the exact figure is not "
            "published."
        )
    else:
        rows, explicit_failed = _explicit_area_rows(
            [r for r in records if r.level != "national"], occupations, tables, detailed=detailed
        )
        failed += explicit_failed

    national = [
        _wage_row(r, "us", "United States", detailed=detailed)
        for r in _rows_for(tables["national"], occupations, None)
    ]
    sources = [
        OEWS_CURRENT_URL.format(soc_digits=occ.replace("-", ""))
        if year == latest
        else OEWS_TABLES_URL
        for occ in occupations
    ]
    return OccupationWagesResponse(
        reference_period=f"May {year}",
        rows=rows,
        national=national,
        failed=failed,
        notes=notes,
        sources=sorted(set(sources)),
    )
