"""Response models for places, occupations, wages (OEWS) and area employment (QCEW).

Like ``models.series``, these are the tool output schemas the client model reads, so field
descriptions state meaning and units.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from econ_gateway.models.series import FailedItem

AreaLevel = Literal["national", "state", "metro", "county"]
WageMeasure = Literal[
    "annual_median", "annual_mean", "hourly_median", "hourly_mean", "employment",
    "location_quotient",
]  # fmt: skip


class LausSeries(BaseModel):
    """LAUS (Local Area Unemployment Statistics) series IDs for an area; pass to ``get_latest``.

    Attributes:
        unemployment_rate: Unemployment rate, percent.
        unemployed: Number unemployed.
        employed: Number employed (household-based).
        labor_force: Civilian labor force.
        seasonal_adjustment: ``SA`` for states and the nation; ``NSA`` for metros and counties.
    """

    unemployment_rate: str
    unemployed: str
    employed: str
    labor_force: str
    seasonal_adjustment: Literal["SA", "NSA"]


class AreaMatch(BaseModel):
    """A place that matched a ``find_area`` query.

    Attributes:
        area_id: Pass to ``get_occupation_wages`` / ``get_area_employment``.
        name: Official area name.
        level: Geographic level.
        has_occupation_wages: Whether OEWS publishes wages for this area (not for counties).
        has_area_employment: Whether QCEW publishes jobs and wages for this area.
        laus: Unemployment series IDs for ``get_latest`` / ``get_series``.
        score: Match strength (higher is better); compare candidates, not absolute values.
    """

    area_id: str
    name: str
    level: AreaLevel
    has_occupation_wages: bool
    has_area_employment: bool
    laus: LausSeries | None = None
    score: float


class FindAreaResponse(BaseModel):
    """Result of ``find_area``.

    Attributes:
        query: The query as received.
        results: Candidates, best first.
        notes: Guidance, e.g. when the query is ambiguous.
    """

    query: str
    results: list[AreaMatch]
    notes: list[str] = Field(default_factory=list)


class OccupationMatch(BaseModel):
    """An occupation that matched a ``find_occupation`` query.

    Attributes:
        occupation_code: SOC code; pass to ``get_occupation_wages``.
        title: Official SOC title.
        group: ``total``, ``major`` (e.g. all healthcare practitioners), ``minor``, ``broad`` or
            ``detailed`` (a single occupation such as Registered Nurses).
        score: Match strength (higher is better).
    """

    occupation_code: str
    title: str
    group: str
    score: float


class FindOccupationResponse(BaseModel):
    """Result of ``find_occupation``.

    Attributes:
        query: The query as received.
        results: Candidates, best first.
    """

    query: str
    results: list[OccupationMatch]


class WageRow(BaseModel):
    """OEWS employment and wages for one occupation in one area.

    Dollar fields are ``None`` when not published; ``top_coded`` lists fields whose true value is
    at or above the OEWS reporting cap (the source does not publish the number).

    Attributes:
        area_id: Area (see ``find_area``).
        area_name: Official area name.
        occupation_code: SOC code.
        occupation: SOC title.
        employment: Estimated jobs in the occupation, rounded by BLS.
        jobs_per_1000: Occupation jobs per 1,000 jobs in the area.
        location_quotient: Concentration vs the nation (1.0 = same share; >1 = more concentrated).
        hourly_mean: Mean hourly wage, dollars.
        hourly_median: Median hourly wage, dollars.
        annual_mean: Mean annual wage, dollars.
        annual_median: Median annual wage, dollars.
        annual_p10: 10th-percentile annual wage, dollars.
        annual_p25: 25th-percentile annual wage, dollars (detailed format).
        annual_p75: 75th-percentile annual wage, dollars (detailed format).
        annual_p90: 90th-percentile annual wage, dollars.
        annual_only: True when BLS publishes only annual wages (e.g. teachers); hourly is ``None``.
        top_coded: Fields at or above the OEWS cap.
    """

    area_id: str
    area_name: str
    occupation_code: str
    occupation: str
    employment: float | None = None
    jobs_per_1000: float | None = None
    location_quotient: float | None = None
    hourly_mean: float | None = None
    hourly_median: float | None = None
    annual_mean: float | None = None
    annual_median: float | None = None
    annual_p10: float | None = None
    annual_p25: float | None = None
    annual_p75: float | None = None
    annual_p90: float | None = None
    annual_only: bool = False
    top_coded: list[str] = Field(default_factory=list)


class OccupationWagesResponse(BaseModel):
    """Result of ``get_occupation_wages``.

    Attributes:
        reference_period: OEWS reference period, e.g. ``May 2025``.
        rows: Requested occupation × area rows (ranked by ``sort_by`` when ranking).
        national: National rows for the same occupations, for comparison.
        failed: Requested combinations that could not be returned, with next steps.
        notes: How to read the figures (reference period, wage definitions, ranking scope).
        sources: Citation links for the occupations returned.
    """

    reference_period: str
    rows: list[WageRow]
    national: list[WageRow] = Field(default_factory=list)
    failed: list[FailedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class EmploymentRow(BaseModel):
    """QCEW establishments, jobs and pay for one area × industry in one quarter.

    Attributes:
        area_id: Area (see ``find_area``).
        area_name: Official area name.
        industry: Industry key as requested.
        industry_title: What the key covers.
        ownership: ``total``, ``private``, ``federal``, ``state`` or ``local`` government.
        disclosed: False when BLS suppresses the cell for confidentiality; all values are then
            ``None`` (the source reports zeros, which are *not* real zeros).
        establishments: Establishments in the quarter.
        employment: Jobs in the third month of the quarter.
        avg_weekly_wage: Average weekly wage, dollars.
        employment_change_year_ago: Change in jobs vs the same quarter a year earlier.
        employment_pct_change_year_ago: Percent change in jobs vs a year earlier.
        avg_weekly_wage_pct_change_year_ago: Percent change in average weekly wage, year on year.
    """

    area_id: str
    area_name: str
    industry: str
    industry_title: str
    ownership: str
    disclosed: bool
    establishments: int | None = None
    employment: int | None = None
    avg_weekly_wage: float | None = None
    employment_change_year_ago: int | None = None
    employment_pct_change_year_ago: float | None = None
    avg_weekly_wage_pct_change_year_ago: float | None = None


class AreaEmploymentResponse(BaseModel):
    """Result of ``get_area_employment``.

    Attributes:
        reference_period: Quarter covered, e.g. ``2026-Q1``.
        rows: One row per area × industry.
        failed: Requested areas or industries that could not be returned, with next steps.
        notes: How to read QCEW figures (coverage, seasonality, ownership choices).
        sources: Exact QCEW files the figures came from.
    """

    reference_period: str
    rows: list[EmploymentRow]
    failed: list[FailedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
