"""Area jobs and pay from QCEW (``get_area_employment``).

QCEW (Quarterly Census of Employment and Wages) is a near-census of jobs covered by
unemployment insurance (~95% of U.S. jobs), by county, metro, state and industry. Rules the
code applies (DESIGN.md decision 15):

- Suppressed cells (``disclosure_code == "N"``) are published as zeros; they come back as
  ``disclosed: false`` with null values, never as 0.
- Totals across ownerships exist only for "all industries"; sector rows are private ownership.
- The latest quarter is discovered by stepping back from today (QCEW lags about 5-6 months).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

import pandas as pd

from econ_gateway.consts import QCEW_AREA_URL, QCEW_QUARTERS_TO_TRY
from econ_gateway.models import AreaEmploymentResponse, EmploymentRow, FailedItem
from econ_gateway.services.areas import AreaRecord, resolve_area_ids
from econ_gateway.services.errors import DataNotAvailableError, InvalidRequestError
from econ_gateway.services.upstream import Upstream

IndustryKey = Literal[
    "total", "private", "federal_government", "state_government", "local_government",
    "goods_producing", "service_providing", "natural_resources_mining", "construction",
    "manufacturing", "trade_transportation_utilities", "retail_trade", "information",
    "financial_activities", "professional_business_services",
    "professional_scientific_technical", "education_health_services",
    "health_care_social_assistance", "leisure_hospitality", "accommodation_food_services",
    "other_services",
]  # fmt: skip


@dataclass(frozen=True)
class Industry:
    """A QCEW industry × ownership slice.

    Attributes:
        code: QCEW industry code (``10`` = all industries; ``101x``/``102x`` supersectors; NAICS).
        own_code: Ownership: 0 total, 1 federal, 2 state, 3 local government, 5 private.
        title: What the slice covers, for the response.
        ownership: Ownership in words.
    """

    code: str
    own_code: str
    title: str
    ownership: str


def _private(code: str, title: str) -> Industry:
    return Industry(code, "5", title, "private")


INDUSTRIES: dict[str, Industry] = {
    "total": Industry("10", "0", "All industries, all ownerships", "total"),
    "private": _private("10", "All industries, private sector"),
    "federal_government": Industry("10", "1", "Federal government", "federal"),
    "state_government": Industry("10", "2", "State government", "state"),
    "local_government": Industry("10", "3", "Local government", "local"),
    "goods_producing": _private("101", "Goods-producing industries"),
    "service_providing": _private("102", "Service-providing industries"),
    "natural_resources_mining": _private("1011", "Natural resources and mining"),
    "construction": _private("1012", "Construction"),
    "manufacturing": _private("1013", "Manufacturing"),
    "trade_transportation_utilities": _private("1021", "Trade, transportation, and utilities"),
    "retail_trade": _private("44-45", "Retail trade (NAICS 44-45)"),
    "information": _private("1022", "Information"),
    "financial_activities": _private("1023", "Financial activities"),
    "professional_business_services": _private("1024", "Professional and business services"),
    "professional_scientific_technical": _private(
        "54", "Professional, scientific, and technical services (NAICS 54)"
    ),
    "education_health_services": _private("1025", "Education and health services"),
    "health_care_social_assistance": _private("62", "Health care and social assistance (NAICS 62)"),
    "leisure_hospitality": _private("1026", "Leisure and hospitality"),
    "accommodation_food_services": _private("72", "Accommodation and food services (NAICS 72)"),
    "other_services": _private("1027", "Other services"),
}
# IndustryKey (the tool's input enum) and INDUSTRIES must list the same keys; a test checks it.
INDUSTRY_KEYS: tuple[str, ...] = get_args(IndustryKey)


# ---------------------------------------------------------------- quarter discovery


def _previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year, quarter - 1) if quarter > 1 else (year - 1, 4)


def latest_qcew_quarter(upstream: Upstream, area_code: str, today: date) -> tuple[int, int]:
    """Newest (year, quarter) published for ``area_code``, stepping back from today.

    The current quarter is never published yet, so the search starts one quarter earlier.

    Raises:
        DataNotAvailableError: When none of the last ``QCEW_QUARTERS_TO_TRY`` quarters exist.
    """
    year, quarter = _previous_quarter(today.year, (today.month - 1) // 3 + 1)
    for _ in range(QCEW_QUARTERS_TO_TRY):
        try:
            upstream.qcew_area(year, quarter, area_code)
            return year, quarter
        except DataNotAvailableError:
            year, quarter = _previous_quarter(year, quarter)
    raise DataNotAvailableError(
        f"No QCEW quarter in the last {QCEW_QUARTERS_TO_TRY} was found for area {area_code}."
    )


# ---------------------------------------------------------------- rows


def _number(row: dict[str, object], column: str) -> float | None:
    value = row.get(column)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)  # type: ignore[arg-type]  # numeric QCEW columns


def _int(row: dict[str, object], column: str) -> int | None:
    value = _number(row, column)
    return None if value is None else int(value)


def _employment_row(row: dict[str, object], record: AreaRecord, key: str) -> EmploymentRow:
    industry = INDUSTRIES[key]
    suppressed = EmploymentRow(
        area_id=record.area_id,
        area_name=record.name,
        industry=key,
        industry_title=industry.title,
        ownership=industry.ownership,
        disclosed=False,
    )
    if row.get("disclosure_code") == "N":  # confidential: the zeros in the file are not data
        return suppressed
    oty_disclosed = row.get("oty_disclosure_code") != "N"
    return suppressed.model_copy(
        update={
            "disclosed": True,
            "establishments": _int(row, "qtrly_estabs"),
            "employment": _int(row, "month3_emplvl"),
            "avg_weekly_wage": _number(row, "avg_wkly_wage"),
            "employment_change_year_ago": (
                _int(row, "oty_month3_emplvl_chg") if oty_disclosed else None
            ),
            "employment_pct_change_year_ago": (
                _number(row, "oty_month3_emplvl_pct_chg") if oty_disclosed else None
            ),
            "avg_weekly_wage_pct_change_year_ago": (
                _number(row, "oty_avg_wkly_wage_pct_chg") if oty_disclosed else None
            ),
        }
    )


def _rows_for_area(
    frame: pd.DataFrame, record: AreaRecord, keys: list[str]
) -> tuple[list[EmploymentRow], list[FailedItem]]:
    rows: list[EmploymentRow] = []
    failed: list[FailedItem] = []
    for key in keys:
        industry = INDUSTRIES[key]
        match = frame.loc[
            (frame["industry_code"] == industry.code) & (frame["own_code"] == industry.own_code)
        ]
        if match.empty:
            failed.append(
                FailedItem(
                    id=f"{key}@{record.area_id}",
                    reason=f"QCEW has no '{industry.title}' row for {record.name} this quarter.",
                    hint="The industry may have no covered employers there; try a broader key "
                    "such as 'service_providing' or 'private'.",
                )
            )
            continue
        record_row = {str(k): v for k, v in match.iloc[0].to_dict().items()}
        rows.append(_employment_row(record_row, record, key))
    return rows, failed


# ---------------------------------------------------------------- get_area_employment


def _period(
    year: int | None, quarter: int | None, upstream: Upstream, probe_code: str, today: date
) -> tuple[int, int]:
    if (year is None) != (quarter is None):
        raise InvalidRequestError(
            "Give both year and quarter (e.g. year=2025, quarter=4), or neither for the latest."
        )
    if year is not None and quarter is not None:
        return year, quarter
    return latest_qcew_quarter(upstream, probe_code, today)


def get_area_employment(
    area_ids: list[str],
    upstream: Upstream,
    *,
    industries: list[str] | None = None,
    year: int | None = None,
    quarter: int | None = None,
    today: date | None = None,
) -> AreaEmploymentResponse:
    """QCEW establishments, jobs and weekly wages for areas × industries in one quarter.

    Args:
        area_ids: Places from ``find_area`` (county, metro, state or ``us``).
        upstream: Data access.
        industries: Keys of ``INDUSTRIES``; default ``["total"]``.
        year: Calendar year; give with ``quarter``, or omit both for the latest.
        quarter: 1-4.
        today: Override for tests.

    Returns:
        One row per area × industry, failures, reading notes and the source files used.
    """
    today = today or date.today()
    keys = list(dict.fromkeys(industries or ["total"]))
    records, failed = resolve_area_ids(area_ids, upstream)
    usable = [r for r in records if r.qcew_code]
    failed += [
        FailedItem(
            id=r.area_id,
            reason=f"QCEW publishes no data for {r.name}.",
            hint="Use the state or a county inside it (find_area with level='county').",
        )
        for r in records
        if not r.qcew_code
    ]
    if not usable:
        return AreaEmploymentResponse(reference_period="n/a", rows=[], failed=failed)

    year, quarter = _period(year, quarter, upstream, usable[0].qcew_code or "", today)
    rows: list[EmploymentRow] = []
    sources: list[str] = []
    for record in usable:
        code = record.qcew_code or ""
        try:
            frame = upstream.qcew_area(year, quarter, code)
        except DataNotAvailableError as exc:
            failed.append(
                FailedItem(id=record.area_id, reason=str(exc), hint="Try an earlier quarter.")
            )
            continue
        area_rows, area_failed = _rows_for_area(frame, record, keys)
        rows += area_rows
        failed += area_failed
        sources.append(QCEW_AREA_URL.format(year=year, quarter=quarter, area=code))

    notes = [
        f"QCEW {year}-Q{quarter}: jobs covered by unemployment insurance (about 95% of U.S. "
        "jobs; excludes most self-employed). employment is the third month of the quarter.",
        "QCEW is not seasonally adjusted: compare with the same quarter a year earlier "
        "(the *_year_ago fields), not with the previous quarter.",
    ]
    if any(INDUSTRIES[k].ownership == "private" and k != "private" for k in keys):
        notes.append("Industry rows are private-sector only, as QCEW publishes them.")
    if any(not r.disclosed for r in rows):
        notes.append(
            "disclosed=false rows are suppressed by BLS to protect employer confidentiality; "
            "their values are unknown, not zero."
        )
    return AreaEmploymentResponse(
        reference_period=f"{year}-Q{quarter}",
        rows=rows,
        failed=failed,
        notes=notes,
        sources=sources,
    )
