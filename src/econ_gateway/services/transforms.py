"""Date-aware transforms and period labels for time series.

Semantics match Project 1's ``labor_agent.analysis.metrics`` (DESIGN.md decision 3): "year
ago" means the observation dated exactly one calendar year earlier, not 12 rows earlier, so
gaps in the data never shift comparisons. Weekly data is matched to the nearest observation
within ``WEEKLY_MATCH_TOLERANCE_DAYS`` because week-ending weekdays move between years.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TypeVar

import pandas as pd

from econ_gateway.consts import TRANSFORM_DECIMALS, WEEKLY_MATCH_TOLERANCE_DAYS
from econ_gateway.models import Transform

# Median spacing in days -> frequency code. Boundaries sit between natural spacings.
_SPACING_TO_FREQ = ((3, "D"), (10, "W"), (20, "BW"), (45, "M"), (120, "Q"), (250, "S"))
_DAILY_LIKE = {"D", "W", "BW"}

DateT = TypeVar("DateT", date, datetime)


def as_datetimes(index: pd.Index) -> list[datetime]:
    """Index values as plain ``datetime`` objects (keeps date handling precisely typed)."""
    return [ts.to_pydatetime() for ts in pd.DatetimeIndex(index)]


def minus_years(day: DateT, years: int) -> DateT:
    """Same calendar day ``years`` earlier; Feb 29 maps to Feb 28 in non-leap years."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def infer_freq_code(dates: pd.Index) -> str:
    """Infer ``D``/``W``/``BW``/``M``/``Q``/``S``/``A`` from the median spacing of ``dates``."""
    index = pd.DatetimeIndex(dates).sort_values().unique()
    if len(index) < 2:
        return "M"  # a single point carries no spacing; monthly is the most common case
    median_days = float(pd.Series(index).diff().dt.days.median())
    for limit, code in _SPACING_TO_FREQ:
        if median_days < limit:
            return code
    return "A"


def period_label(day: datetime | date, freq_code: str) -> str:
    """Readable period: ``2026-09`` (monthly), ``2026-Q3``, ``2026-H2``, ``2026``, else a date."""
    if freq_code == "M":
        return f"{day:%Y-%m}"
    if freq_code == "Q":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    if freq_code == "S":
        return f"{day.year}-H{1 if day.month <= 6 else 2}"
    if freq_code == "A":
        return f"{day.year}"
    return f"{day:%Y-%m-%d}"


def year_ago(series: pd.Series, freq_code: str) -> pd.Series:
    """Values from one calendar year earlier, aligned to ``series.index`` (NaN when missing)."""
    index = pd.DatetimeIndex(series.index)
    target = index - pd.DateOffset(years=1)
    if freq_code in _DAILY_LIKE:
        prior = series.sort_index().reindex(
            target, method="nearest", tolerance=pd.Timedelta(days=WEEKLY_MATCH_TOLERANCE_DAYS)
        )
    else:
        prior = series.reindex(target)
    return pd.Series(prior.to_numpy(), index=index, name=series.name)


def apply_transform(series: pd.Series, transform: Transform, freq_code: str) -> pd.Series:
    """Apply ``transform`` to a date-indexed series.

    Args:
        series: Values indexed by period date, sorted ascending.
        transform: One of the ``Transform`` literals.
        freq_code: Frequency code from ``infer_freq_code``.

    Returns:
        Transformed series on the same index. Computed values are rounded to
        ``TRANSFORM_DECIMALS``; ``level`` returns the published values untouched.
    """
    if transform == "level":
        return series
    if transform == "pct_change_year_ago":
        out = (series / year_ago(series, freq_code) - 1) * 100
    elif transform == "change_year_ago":
        out = series - year_ago(series, freq_code)
    elif transform == "pct_change_prior_period":
        out = (series / series.shift(1) - 1) * 100
    else:  # change_prior_period
        out = series - series.shift(1)
    return out.round(TRANSFORM_DECIMALS)


# (from, to) -> observations in one complete target period, and the pandas resample rule.
_AGGREGATIONS = {("M", "Q"): (3, "QS"), ("M", "A"): (12, "YS"), ("Q", "A"): (4, "YS")}


def to_frequency(series: pd.Series, source_freq: str, target_freq: str) -> pd.Series | None:
    """Average ``series`` up to ``target_freq``, keeping only complete periods.

    Example: monthly CPI -> quarterly means for the quarterly Employment Cost Index. A quarter
    with only one or two months published is dropped rather than compared as if complete.

    Returns:
        The aggregated series, ``series`` unchanged when frequencies match, or ``None`` when
        the conversion is not supported (e.g. quarterly -> monthly).
    """
    if source_freq == target_freq:
        return series
    spec = _AGGREGATIONS.get((source_freq, target_freq))
    if spec is None:
        return None
    needed, rule = spec
    grouped = series.dropna().resample(rule)
    means, counts = grouped.mean(), grouped.count()
    return means.loc[counts == needed]


def transform_units(transform: Transform, units: str, *, rate: bool) -> str:
    """Units of values after ``transform`` (changes in a rate are percentage points)."""
    if transform == "level":
        return units
    base = "year ago" if transform.endswith("year_ago") else "prior period"
    if transform.startswith("pct_change"):
        return f"percent change from {base}"
    return f"change from {base} ({'percentage points' if rate else units})"
