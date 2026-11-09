"""Rank several series on one measure (``compare_series``).

Typical use: "which industries had the biggest wage gains over the past year, and how does
that compare to inflation?" means 10 average-hourly-earnings series ranked by
``pct_change_year_ago``, with CPI as the benchmark. One call, no arithmetic left to the model.

Conventions (DESIGN.md decision 20):

- ``since="2019"`` compares with the 2019 *average*; ``since="2019-06"`` with that period.
- Percent changes of rates are refused per row (they mislead); use ``change_*`` instead.
- Different units, or series ending in different periods, are flagged in ``notes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from econ_gateway.consts import TRANSFORM_DECIMALS
from econ_gateway.models import CompareMeasure, CompareResponse, CompareRow, FailedItem
from econ_gateway.services.errors import InvalidRequestError
from econ_gateway.services.metadata import is_rate
from econ_gateway.services.observations import (
    RawSeries,
    fetch_raw,
    meta_or_failure,
    normalize_ids,
    parse_date,
    stale_notes,
)
from econ_gateway.services.transforms import as_datetimes, minus_years, period_label, year_ago
from econ_gateway.services.upstream import Upstream

_PCT_MEASURES = {"pct_change_year_ago", "pct_change_since"}
_SINCE_MEASURES = {"change_since", "pct_change_since"}
LOOKBACK_YEARS = 2  # history fetched for year-ago measures


@dataclass(frozen=True)
class _Base:
    """The comparison point for a change measure."""

    period: str
    value: float


def _year_ago_base(values: pd.Series, freq: str) -> _Base | None:
    prior = year_ago(values, freq).iloc[-1]
    if pd.isna(prior):
        return None
    latest_day = as_datetimes(values.index)[-1]
    return _Base(period_label(minus_years(latest_day, 1), freq), float(prior))


def _since_base(values: pd.Series, freq: str, since: str) -> _Base | None:
    """Base for ``*_since``: a year's average for ``YYYY``, else the period at ``since``."""
    index = pd.DatetimeIndex(values.index)
    if len(since) == 4:
        in_year = values.loc[index.year == int(since)]
        return None if in_year.empty else _Base(f"{since} average", float(in_year.mean()))
    upto = values.loc[: pd.Timestamp(parse_date(since, end=False))]
    if upto.empty:
        return None
    return _Base(period_label(as_datetimes(upto.index)[-1], freq), float(upto.iloc[-1]))


def _value_units(measure: CompareMeasure, units: str, rate: bool) -> str:
    if measure == "latest_value":
        return units
    if measure in _PCT_MEASURES:
        return "percent"
    return "percentage points" if rate else units


def _row(
    raw: RawSeries, meta_row: CompareRow, measure: CompareMeasure, since: str | None
) -> CompareRow:
    """Fill ``value`` / ``base_*`` for one series (``meta_row`` holds meta and latest)."""
    values = raw.values.dropna()
    if measure == "latest_value":
        return meta_row.model_copy(update={"value": meta_row.latest_value})
    base = (
        _since_base(values, raw.freq_code, since or "")
        if measure in _SINCE_MEASURES
        else _year_ago_base(values, raw.freq_code)
    )
    if base is None:
        return meta_row
    latest = meta_row.latest_value
    if measure in _PCT_MEASURES:
        value = None if base.value == 0 else (latest / base.value - 1) * 100
    else:
        value = latest - base.value
    return meta_row.model_copy(
        update={
            "base_period": base.period,
            "base_value": round(base.value, TRANSFORM_DECIMALS),
            "value": None if value is None else round(value, TRANSFORM_DECIMALS),
        }
    )


def _build_rows(
    raws: dict[str, RawSeries], ids: list[str], upstream: Upstream, measure: CompareMeasure,
    since: str | None,
) -> tuple[dict[str, CompareRow], list[FailedItem], list[str]]:  # fmt: skip
    rows: dict[str, CompareRow] = {}
    failed: list[FailedItem] = []
    notes: list[str] = []
    for raw in (raws[s] for s in ids if s in raws):
        meta = meta_or_failure(raw, upstream, include_notes=False)
        if isinstance(meta, FailedItem):
            failed.append(meta)
            continue
        values = raw.values.dropna()
        rate = is_rate(meta.units)
        latest_day = as_datetimes(values.index)[-1]
        skeleton = CompareRow(
            meta=meta,
            rank=None,
            value=None,
            value_units=_value_units(measure, meta.units, rate),
            latest_period=period_label(latest_day, raw.freq_code),
            latest_value=float(values.iloc[-1]),
        )
        if rate and measure in _PCT_MEASURES:
            notes.append(
                f"{raw.series_id} is a rate ({meta.units}); its percent change is left out. "
                "Compare rates with change_year_ago or change_since (percentage points)."
            )
            rows[raw.series_id] = skeleton
            continue
        rows[raw.series_id] = _row(raw, skeleton, measure, since)
    return rows, failed, notes


def _align_to_benchmark(raws: dict[str, RawSeries], bench_id: str) -> list[str]:
    """Cut every series at the benchmark's latest period, in place, so gaps compare like with like.

    Found live: October wages were being compared with September CPI because the CPI for
    October was not out yet. A gap between growth rates of different months means nothing.
    """
    bench = raws.get(bench_id)
    if bench is None or bench.values.dropna().empty:
        return []
    cutoff = as_datetimes(bench.values.dropna().index)[-1]
    trimmed = [
        raw.series_id
        for raw in raws.values()
        if raw.series_id != bench_id and as_datetimes(raw.values.dropna().index)[-1] > cutoff
    ]
    for series_id in trimmed:
        raws[series_id].frame = raws[series_id].frame.loc[: pd.Timestamp(cutoff)]
    if not trimmed:
        return []
    cutoff_label = period_label(cutoff, bench.freq_code)
    return [
        f"Compared at {cutoff_label}, the benchmark's latest period, so every gap uses the same "
        f"months. Newer data exists for {len(trimmed)} series; use get_latest to see it."
    ]


def _rank(rows: list[CompareRow]) -> list[CompareRow]:
    """Highest value first with ranks 1..n; rows without a value follow, unranked."""
    computed = sorted((r for r in rows if r.value is not None), key=lambda r: r.value or 0.0)
    ranked = [r.model_copy(update={"rank": i}) for i, r in enumerate(reversed(computed), start=1)]
    return ranked + [r for r in rows if r.value is None]


def _apply_benchmark(
    rows: list[CompareRow], benchmark: CompareRow, measure: CompareMeasure
) -> tuple[list[CompareRow], list[str]]:
    """Set ``gap_vs_benchmark`` on each row when the units allow a subtraction."""
    if benchmark.value is None:
        return rows, ["The benchmark's measure could not be computed, so no gaps are given."]
    comparable = measure in _PCT_MEASURES or all(
        r.value_units == benchmark.value_units for r in rows if r.value is not None
    )
    if not comparable:
        return rows, ["The benchmark is in different units, so gap_vs_benchmark is not given."]
    return [
        r
        if r.value is None
        else r.model_copy(
            update={"gap_vs_benchmark": round(r.value - benchmark.value, TRANSFORM_DECIMALS)}
        )
        for r in rows
    ], []


def _comparability_notes(rows: list[CompareRow], measure: CompareMeasure) -> list[str]:
    notes: list[str] = []
    periods = {r.latest_period for r in rows}
    if len(periods) > 1:
        notes.append(
            "Series end in different periods (" + ", ".join(sorted(periods)) + "); each is "
            "compared at its own latest period."
        )
    units = {r.value_units for r in rows if r.value is not None}
    if measure not in _PCT_MEASURES and len(units) > 1:
        notes.append(
            "Values are in different units (" + "; ".join(sorted(units)) + "), so the ranking "
            "mixes incomparable numbers. Use a pct_change measure to compare across units."
        )
    return notes


def compare_series(
    series_ids: list[str],
    upstream: Upstream,
    *,
    measure: CompareMeasure,
    since: str | None = None,
    benchmark_series_id: str | None = None,
    today: date | None = None,
) -> CompareResponse:
    """Rank series by ``measure``, optionally against a benchmark.

    Args:
        series_ids: 2+ series IDs from ``search_series``.
        upstream: Data access.
        measure: What to compare.
        since: ``YYYY`` (that year's average) or ``YYYY-MM[-DD]``; required for ``*_since``.
        benchmark_series_id: Series computed the same way and subtracted as ``gap_vs_benchmark``
            (e.g. CPI ``CUSR0000SA0`` to compare wage growth with inflation).
        today: Override for tests.

    Returns:
        Ranked rows (highest first), benchmark row, failures and comparability notes.

    Raises:
        InvalidRequestError: When ``since`` is missing for a ``*_since`` measure.
    """
    if measure in _SINCE_MEASURES and not since:
        raise InvalidRequestError(
            f"measure '{measure}' needs since, e.g. since='2019' (vs the 2019 average) or "
            "since='2020-02' (vs February 2020)."
        )
    today = today or date.today()
    ids = normalize_ids(series_ids)
    bench_id = benchmark_series_id.strip().upper() if benchmark_series_id else None
    all_ids = normalize_ids([*ids, *([bench_id] if bench_id else [])])
    start = date(today.year - LOOKBACK_YEARS, 1, 1)
    if since and measure in _SINCE_MEASURES:
        start = min(start, date(parse_date(since, end=False).year, 1, 1))

    raws, failed = fetch_raw(all_ids, start, today, upstream)
    notes = _align_to_benchmark(raws, bench_id) if bench_id else []
    rows_by_id, meta_failed, row_notes = _build_rows(raws, all_ids, upstream, measure, since)
    notes += row_notes
    failed += meta_failed
    benchmark = rows_by_id.get(bench_id) if bench_id else None
    ordered = _rank([rows_by_id[s] for s in ids if s in rows_by_id and s != bench_id])
    if benchmark is not None:
        ordered, bench_notes = _apply_benchmark(ordered, benchmark, measure)
        notes += bench_notes
    notes += stale_notes(raws.values())
    notes += _comparability_notes(ordered + ([benchmark] if benchmark else []), measure)
    return CompareResponse(
        measure=measure,
        since=since,
        rows=ordered,
        benchmark=benchmark,
        failed=failed,
        notes=notes,
    )
