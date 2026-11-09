"""Response models for the compute tools (``real_wage_growth``, ``compare_series``).

All numbers are computed by the server from official data; field descriptions state units so
the client model can quote them without doing arithmetic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from econ_gateway.models.series import FailedItem, SeriesMeta

Deflator = Literal["cpi_u", "cpi_w", "pce"]
CompareMeasure = Literal[
    "latest_value", "change_year_ago", "pct_change_year_ago", "change_since", "pct_change_since",
]  # fmt: skip


class RealWagePoint(BaseModel):
    """One period of a real-wage calculation.

    Attributes:
        period: Period label (``2026-09``, ``2026-Q3``, ``2026``).
        nominal_wage: Wage as published, in the wage series units.
        nominal_pct_change_year_ago: Wage growth vs a year earlier, percent.
        inflation_pct_change_year_ago: Price growth vs a year earlier, percent.
        real_pct_change_year_ago: Inflation-adjusted wage growth, percent
            (``(1 + nominal) / (1 + inflation) - 1``).
        real_wage: Wage in dollars of ``base_period`` (prices held at that period's level).
    """

    period: str
    nominal_wage: float | None
    nominal_pct_change_year_ago: float | None
    inflation_pct_change_year_ago: float | None
    real_pct_change_year_ago: float | None
    real_wage: float | None


class WindowChange(BaseModel):
    """Cumulative change between the first and last period of the window.

    Attributes:
        start_period: First period with both wage and price data.
        end_period: Last period with both wage and price data.
        nominal_pct_change: Wage change over the window, percent.
        inflation_pct_change: Price change over the window, percent.
        real_pct_change: Inflation-adjusted wage change over the window, percent.
    """

    start_period: str
    end_period: str
    nominal_pct_change: float
    inflation_pct_change: float
    real_pct_change: float


class RealWageResponse(BaseModel):
    """Result of ``real_wage_growth``.

    Attributes:
        wage: The wage series and its citation link.
        deflator: The price index used and its citation link.
        frequency: Frequency of the comparison (the wage series' frequency).
        base_period: Period whose prices ``real_wage`` is expressed in (latest common period).
        latest: The most recent period with all three growth rates.
        real_wage_rising: True when ``latest.real_pct_change_year_ago`` is above zero, i.e. pay
            is outpacing prices over the past year.
        window: Change over the whole requested window.
        points: Per-period values, oldest first (most recent ``max_points``).
        truncated: True when older periods were dropped from ``points``.
        notes: Method choices and caveats (deflator variant, frequency alignment).
    """

    wage: SeriesMeta
    deflator: SeriesMeta
    frequency: str
    base_period: str
    latest: RealWagePoint | None
    real_wage_rising: bool | None
    window: WindowChange | None
    points: list[RealWagePoint]
    truncated: bool
    notes: list[str] = Field(default_factory=list)


class CompareRow(BaseModel):
    """One series in a ``compare_series`` ranking.

    Attributes:
        meta: Series metadata and citation link.
        rank: 1 = highest ``value``; ``None`` when the measure could not be computed.
        value: The compared measure, in ``value_units``.
        value_units: Units of ``value``.
        latest_period: Most recent period used.
        latest_value: Value in ``latest_period``, in the series units.
        base_period: Comparison period (a year earlier, or ``since``), when the measure has one.
        base_value: Value in ``base_period`` (a yearly average when ``since`` is a year).
        gap_vs_benchmark: ``value`` minus the benchmark's ``value``, in ``value_units``
            (percentage points for percent measures).
    """

    meta: SeriesMeta
    rank: int | None
    value: float | None
    value_units: str
    latest_period: str
    latest_value: float
    base_period: str | None = None
    base_value: float | None = None
    gap_vs_benchmark: float | None = None


class CompareResponse(BaseModel):
    """Result of ``compare_series``.

    Attributes:
        measure: The measure compared.
        since: The ``since`` argument, when used.
        rows: Series ranked by ``value``, highest first; uncomputable rows last.
        benchmark: The benchmark series computed the same way, when requested.
        failed: Series that could not be fetched, with next steps.
        notes: Comparability warnings (different units, different latest periods, rates).
    """

    measure: CompareMeasure
    since: str | None = None
    rows: list[CompareRow]
    benchmark: CompareRow | None = None
    failed: list[FailedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
