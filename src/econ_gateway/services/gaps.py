"""Periods the source never published, and the notes that tell the model about them.

Agencies sometimes skip a period entirely. BLS, for example, published no October 2025
household-survey or CPI data because of the federal government shutdown. The data then has a
hole: BLS returns ``-`` for that month, and the gateway reports ``null``.

The live evals (DESIGN.md §11, decision 41) showed what goes wrong without a clear explanation.
Asked for the change over the past year, the model compared with a neighbouring month and either
said nothing about the gap or labelled the neighbour's value as the missing month. The notes
here therefore (1) say plainly which periods have no published value, (2) give the nearest
published periods with their values, so a fallback comparison is labelled correctly, and (3)
say which computed changes are missing because their comparison period is missing.

Functions take plain pandas series (period date → value, NaN where unpublished) so they can be
used by any service without import cycles.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from econ_gateway.services.transforms import as_datetimes, period_label

MAX_LISTED = 6  # periods named in one note; more are summarized as "and N more"


def _listing(labels: list[str]) -> str:
    shown = ", ".join(labels[:MAX_LISTED])
    extra = len(labels) - MAX_LISTED
    return f"{shown} and {extra} more" if extra > 0 else shown


def unpublished_periods(
    values: pd.Series, freq_code: str, start: date | None = None, end: date | None = None
) -> list[str]:
    """Labels of interior periods with no value: after the first and before the last published one.

    Leading and trailing NaN are not gaps (the window simply starts or ends there).
    """
    published = values.dropna()
    if published.empty:
        return []
    inside = values.loc[published.index[0] : published.index[-1]]
    if start is not None:
        inside = inside.loc[pd.Timestamp(start) :]
    if end is not None:
        inside = inside.loc[: pd.Timestamp(end)]
    missing = inside.loc[inside.isna()]
    return [period_label(day, freq_code) for day in as_datetimes(missing.index)]


def nearest_published(
    values: pd.Series, target: datetime, freq_code: str
) -> tuple[tuple[str, float] | None, tuple[str, float] | None]:
    """The published periods just before and just after ``target``, as ``(label, value)``."""
    published = values.dropna()
    before = published.loc[: pd.Timestamp(target) - pd.Timedelta(days=1)]
    after = published.loc[pd.Timestamp(target) + pd.Timedelta(days=1) :]

    def pick(series: pd.Series, index: int) -> tuple[str, float] | None:
        if series.empty:
            return None
        day = as_datetimes(series.index)[index]
        return period_label(day, freq_code), float(series.iloc[index])

    return pick(before, -1), pick(after, 0)


def missing_year_ago_note(values: pd.Series, year_ago: datetime, freq_code: str) -> str:
    """Note for ``get_latest`` when the same period a year earlier has no published value."""
    label = period_label(year_ago, freq_code)
    before, after = nearest_published(values, year_ago, freq_code)
    neighbours = [f"{near[0]} ({near[1]:g})" for near in (before, after) if near is not None]
    nearest = (
        f" The nearest published periods are {' and '.join(neighbours)}." if neighbours else ""
    )
    return (
        f"The source has no published value for {label}, so the change from a year earlier "
        f"cannot be computed and the year_ago fields are null.{nearest} Tell the user that "
        f"{label} was not published; if you compare with another period instead, name that "
        f"period and never present its value as {label}."
    )


def unpublished_note(series_id: str, labels: list[str]) -> str:
    """Note for ``get_series`` naming periods in the window with no published value."""
    return (
        f"{series_id}: the source has no published value for {_listing(labels)} (null in the "
        "data). Say these periods were not published; never present a neighbouring period's "
        "value under their label."
    )


def missing_comparison_note(series_id: str, labels: list[str], transform: str) -> str:
    """Note for ``get_series`` when a change is null because its comparison period is missing."""
    return (
        f"{series_id}: {transform} is null for {_listing(labels)} because the period it compares "
        "with has no published value."
    )


def missing_comparisons(
    raw: pd.Series,
    transformed: pd.Series,
    freq_code: str,
    start: date,
    end: date,
    *,
    year_ago: bool,
) -> list[str]:
    """Periods in the window that have a value but no computed change (comparison missing).

    Only periods whose comparison period falls inside the published history count, so the
    first year of a series that starts inside the window is not reported as a gap.

    Args:
        raw: Published values (NaN where unpublished).
        transformed: The transform of ``raw`` (NaN where it could not be computed).
        freq_code: Frequency code, for labels.
        start: First date of the window.
        end: Last date of the window.
        year_ago: True for year-over-year transforms, False for prior-period ones.
    """
    published = raw.dropna()
    if published.empty:
        return []
    first = pd.Timestamp(published.index[0])
    earliest = first + (pd.DateOffset(years=1) if year_ago else pd.Timedelta(days=1))
    window = slice(max(pd.Timestamp(start), earliest), pd.Timestamp(end))
    gap = raw.loc[window].notna() & transformed.loc[window].isna()
    return [period_label(day, freq_code) for day in as_datetimes(gap.loc[gap].index)]
