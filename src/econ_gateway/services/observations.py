"""Fetch observations for ``get_series`` and ``get_latest``.

Flow: dedupe IDs → split by source → one batched BLS request plus one request per FRED series
→ per-series metadata → transform → trim to the window → enforce size limits → response model.
One bad ID never fails the whole call: it lands in ``failed`` with a next step.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from econ_gateway.consts import (
    DEFAULT_LOOKBACK_YEARS,
    LATEST_LOOKBACK_YEARS,
    RECENT_END_YEARS,
    TOTAL_POINTS_LIMIT,
    TRANSFORM_DECIMALS,
)
from econ_gateway.models import (
    FailedItem,
    GetLatestResponse,
    GetSeriesResponse,
    LatestValue,
    Observation,
    ResponseFormat,
    SeriesData,
    SeriesMeta,
    Source,
    Transform,
)
from econ_gateway.services.errors import (
    ConfigurationError,
    GatewayError,
    InvalidRequestError,
    SeriesNotFoundError,
)
from econ_gateway.services.gaps import (
    missing_comparison_note,
    missing_comparisons,
    missing_year_ago_note,
    unpublished_note,
    unpublished_periods,
)
from econ_gateway.services.metadata import is_rate, lookup_meta
from econ_gateway.services.transforms import (
    apply_transform,
    as_datetimes,
    infer_freq_code,
    minus_years,
    period_label,
    transform_units,
    year_ago,
)
from econ_gateway.services.upstream import STALE_COLUMN, Upstream

# BLS annual averages of sub-annual series (M13, Q05, S03). Only requested when asked for, but
# dropped defensively so they never appear as an extra "January" observation.
_BLS_ANNUAL_AVERAGE_PERIODS = ["M13", "Q05", "S03"]
NOT_FOUND_HINT = (
    "Check the ID with describe_series, or call search_series to find a valid one. "
    "If the ID is right, the source may not have published data for this window."
)


@dataclass
class RawSeries:
    """Observations for one series as fetched, before transforms.

    Attributes:
        series_id: Upper-cased series ID.
        source: Publishing system.
        frame: Indexed by period date (ascending) with columns ``value`` (float, NaN where the
            source has no value), ``preliminary`` (bool) and ``footnotes`` (str, "" when none).
            One frame keeps the three aligned through every slice.
        freq_code: Frequency code inferred from the dates.
        stale_as_of: UTC time of the cached copy when the source was unreachable, else ``None``.
    """

    series_id: str
    source: Source
    frame: pd.DataFrame
    freq_code: str
    stale_as_of: str | None = None

    @property
    def values(self) -> pd.Series:
        """The ``value`` column."""
        return pd.Series(self.frame["value"])  # wrapping narrows the type; no data is copied

    @property
    def dates(self) -> list[datetime]:
        """Period dates as plain datetimes."""
        return as_datetimes(self.frame.index)


# ---------------------------------------------------------------- dates


def parse_date(text: str, *, end: bool) -> date:
    """Parse ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``.

    Partial dates expand to the start of the period, or to its end when ``end`` is True.
    """
    parts = [int(p) for p in text.split("-")]
    try:
        if len(parts) == 1:
            return date(parts[0], 12, 31) if end else date(parts[0], 1, 1)
        if len(parts) == 2:
            last_day = calendar.monthrange(parts[0], parts[1])[1]
            return date(parts[0], parts[1], last_day if end else 1)
        return date(parts[0], parts[1], parts[2])
    except ValueError as exc:  # also raised by monthrange for month 13
        raise InvalidRequestError(
            f"'{text}' is not a valid date. Use YYYY, YYYY-MM or YYYY-MM-DD, e.g. '2019-06'."
        ) from exc


def resolve_window(start_date: str | None, end_date: str | None, today: date) -> tuple[date, date]:
    """Turn optional user dates into a concrete, validated window."""
    start = (
        parse_date(start_date, end=False)
        if start_date
        else date(today.year - DEFAULT_LOOKBACK_YEARS, 1, 1)
    )
    end = min(parse_date(end_date, end=True), today) if end_date else today
    if start > end:
        raise InvalidRequestError(
            f"start_date {start.isoformat()} is after end_date {end.isoformat()}. "
            "Swap them, or omit end_date to get data up to the latest release."
        )
    return start, end


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- fetching


def normalize_ids(series_ids: list[str]) -> list[str]:
    """Upper-case, strip and de-duplicate IDs, keeping the caller's order."""
    return list(dict.fromkeys(s.strip().upper() for s in series_ids if s.strip()))


def _raw_series(series_id: str, source: Source, part: pd.DataFrame) -> RawSeries:
    """Build a ``RawSeries`` from rows with ``date``, ``value`` and optional ``footnotes``."""
    part = part.sort_values(by="date").drop_duplicates(subset="date", keep="last")
    footnotes = (
        part["footnotes"].fillna("").astype(str).to_numpy()
        if "footnotes" in part.columns
        else [""] * len(part)
    )
    frame = pd.DataFrame(
        {"value": part["value"].to_numpy(dtype=float), "footnotes": footnotes},
        index=pd.DatetimeIndex(part["date"]),
    )
    # BLS marks preliminary values with footnote code P, text "preliminary". FRED has no flags.
    frame["preliminary"] = frame["footnotes"].str.contains("preliminary", case=False)
    stale = part[STALE_COLUMN].dropna() if STALE_COLUMN in part.columns else pd.Series()
    return RawSeries(
        series_id,
        source,
        frame,
        infer_freq_code(frame.index),
        stale_as_of=str(stale.iloc[0]) if not stale.empty else None,
    )


def stale_notes(raws: Iterable[RawSeries]) -> list[str]:
    """One note naming the series served from cache because the source was unreachable."""
    stale = [raw for raw in raws if raw.stale_as_of]
    if not stale:
        return []
    oldest = min(raw.stale_as_of or "" for raw in stale)
    ids = ", ".join(raw.series_id for raw in stale)
    return [
        f"The source could not be reached, so {ids} come from the gateway's cache (fetched as "
        f"early as {oldest} UTC; see meta.stale_as_of). A newer release may exist; say so when "
        "quoting these numbers."
    ]


def with_staleness(meta: SeriesMeta, raw: RawSeries) -> SeriesMeta:
    """Copy ``raw.stale_as_of`` onto the metadata the client sees."""
    if raw.stale_as_of is None:
        return meta
    return meta.model_copy(update={"stale_as_of": raw.stale_as_of})


def _bls_reason(series_id: str, messages: list[str]) -> str:
    """Use BLS's own message for this series when it sent one (e.g. 'Series does not exist')."""
    for message in messages:
        if series_id in message:
            return f"BLS: {message}"
    return "BLS returned no observations for this series in the requested window."


def _fetch_bls_batch(
    series_ids: list[str], start: date, end: date, upstream: Upstream
) -> tuple[list[RawSeries], list[FailedItem]]:
    """All BLS IDs in one batched request (the client splits by 50 series / 20 years)."""
    frame, messages = upstream.fetch_bls(series_ids, start.year, end.year)
    found: list[RawSeries] = []
    failed: list[FailedItem] = []
    for series_id in series_ids:
        part = frame.loc[
            (frame["series_id"] == series_id) & ~frame["period"].isin(_BLS_ANNUAL_AVERAGE_PERIODS)
        ]
        if part["value"].dropna().empty:
            reason = _bls_reason(series_id, messages)
            failed.append(FailedItem(id=series_id, reason=reason, hint=NOT_FOUND_HINT))
        else:
            found.append(_raw_series(series_id, "BLS", part))
    return found, failed


def _fetch_fred_one(
    series_id: str, start: date, end: date, upstream: Upstream
) -> RawSeries | FailedItem:
    """One FRED series; failures become a ``FailedItem`` with the right next step."""
    try:
        frame = upstream.fetch_fred(series_id, start.isoformat(), end.isoformat())
    except ConfigurationError as exc:
        return FailedItem(id=series_id, reason=str(exc), hint="Use BLS series instead.")
    except SeriesNotFoundError as exc:
        return FailedItem(id=series_id, reason=str(exc), hint=NOT_FOUND_HINT)
    except GatewayError as exc:
        return FailedItem(id=series_id, reason=str(exc), hint="Retry shortly.")
    if frame["value"].dropna().empty:
        return FailedItem(
            id=series_id,
            reason="FRED returned no observations in the requested window.",
            hint=NOT_FOUND_HINT,
        )
    return _raw_series(series_id, "FRED", frame)


def fetch_raw(
    series_ids: list[str], start: date, end: date, upstream: Upstream
) -> tuple[dict[str, RawSeries], list[FailedItem]]:
    """Fetch observations for many series, collecting per-series failures.

    Args:
        series_ids: Normalized IDs.
        start: First date wanted (BLS is fetched by whole years, then trimmed).
        end: Last date wanted.
        upstream: Data access.

    Returns:
        ``(series by ID, failures)``. Every requested ID appears in exactly one of the two.

    Raises:
        UpstreamError: When BLS fails for the whole batch (outage or quota); FRED failures are
            recorded per series instead because each FRED series is a separate request.
    """
    by_source: dict[Source, list[str]] = {"BLS": [], "FRED": []}
    for series_id in series_ids:
        by_source[upstream.infer_source(series_id)].append(series_id)

    found: list[RawSeries] = []
    failed: list[FailedItem] = []
    if by_source["BLS"]:
        found, failed = _fetch_bls_batch(by_source["BLS"], start, end, upstream)
    for series_id in by_source["FRED"]:
        item = _fetch_fred_one(series_id, start, end, upstream)
        if isinstance(item, FailedItem):
            failed.append(item)
        else:
            found.append(item)

    # Trim whole-year BLS fetches (and any FRED overshoot) to the exact window.
    window = slice(pd.Timestamp(start), pd.Timestamp(end))
    for raw in found:
        raw.frame = raw.frame.loc[window]
    return {raw.series_id: raw for raw in found}, failed


def meta_or_failure(
    raw: RawSeries, upstream: Upstream, *, include_notes: bool
) -> SeriesMeta | FailedItem:
    """Look up metadata; a metadata failure should not lose data we already have."""
    try:
        meta = lookup_meta(
            raw.series_id,
            raw.source,
            upstream,
            include_notes=include_notes,
            frequency_code=raw.freq_code,
        )
        return with_staleness(meta, raw)
    except GatewayError as exc:
        return FailedItem(id=raw.series_id, reason=str(exc), hint="Retry shortly.")


# ---------------------------------------------------------------- get_series


def _to_optional_float(value: float) -> float | None:
    """NaN (no published value) becomes ``None`` so it serializes as JSON null."""
    return None if math.isnan(value) else float(value)


def _series_data(
    raw: RawSeries,
    meta: SeriesMeta,
    *,
    transform: Transform,
    start: date,
    end: date,
    limit: int,
    detailed: bool,
    knows_latest: bool,
) -> SeriesData:
    """Transform, window and truncate one series into its response model.

    ``raw`` may extend past ``end`` (see ``get_series``); values are transformed on the full
    history first, then cut to ``start..end``.
    """
    rate = is_rate(meta.units)
    frame = raw.frame.assign(value=apply_transform(raw.values, transform, raw.freq_code))
    frame = frame.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    available = len(frame)
    shown = RawSeries(raw.series_id, raw.source, frame.tail(limit), raw.freq_code)
    labels = [period_label(d, raw.freq_code) for d in shown.dates]
    values = [_to_optional_float(v) for v in shown.values.to_list()]
    flags = [bool(p) for p in shown.frame["preliminary"].to_list()]

    data = SeriesData(
        meta=meta,
        transform=transform,
        value_units=transform_units(transform, meta.units, rate=rate),
        preliminary_periods=[lbl for lbl, p in zip(labels, flags, strict=True) if p],
        # Unknown when an old end_date stopped the fetch early (see get_series).
        latest_available=_latest_label(raw) if knows_latest else None,
        returned=len(labels),
        available=available,
        truncated=len(labels) < available,
    )
    if detailed:
        footnotes = [str(f) or None for f in shown.frame["footnotes"].to_list()]
        data.observations = [
            Observation(period=lbl, date=f"{d:%Y-%m-%d}", value=v, preliminary=p, footnotes=f)
            for lbl, d, v, p, f in zip(labels, shown.dates, values, flags, footnotes, strict=True)
        ]
    else:
        data.points = list(zip(labels, values, strict=True))
    return data


def _latest_label(raw: RawSeries) -> str | None:
    """Period label of the latest non-missing raw value."""
    present = RawSeries(raw.series_id, raw.source, raw.frame.loc[raw.values.notna()], raw.freq_code)
    dates = present.dates
    return period_label(dates[-1], raw.freq_code) if dates else None


def _window_notes(
    raw: RawSeries, transform: Transform, start: date, end: date, latest: str | None
) -> list[str]:
    """Notes on unpublished periods, missing comparisons and data cut off by ``end``."""
    notes: list[str] = []
    missing = unpublished_periods(raw.values, raw.freq_code, start, end)
    if missing:
        notes.append(unpublished_note(raw.series_id, missing))
    if transform != "level":
        transformed = apply_transform(raw.values, transform, raw.freq_code)
        year_ago_transform = transform.endswith("year_ago")
        no_change = missing_comparisons(
            raw.values, transformed, raw.freq_code, start, end, year_ago=year_ago_transform
        )
        no_change = [label for label in no_change if label not in missing]
        if no_change:
            notes.append(missing_comparison_note(raw.series_id, no_change, transform))
    shown_last = _latest_label(
        RawSeries(raw.series_id, raw.source, raw.frame.loc[: pd.Timestamp(end)], raw.freq_code)
    )
    if latest and shown_last and latest != shown_last:
        notes.append(
            f"{raw.series_id}: newer data exists. The latest published period is {latest}, but "
            f"this response stops at {shown_last} because of end_date. Omit end_date to include "
            "the latest release."
        )
    return notes


def get_series(
    series_ids: list[str],
    upstream: Upstream,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    transform: Transform = "level",
    max_points: int,
    response_format: ResponseFormat = "concise",
    today: date | None = None,
) -> GetSeriesResponse:
    """Observations for several series over a window, optionally transformed.

    Args:
        series_ids: 1+ BLS/FRED IDs (normalized here).
        upstream: Data access.
        start_date: ``YYYY[-MM[-DD]]``; default is Jan 1, ``DEFAULT_LOOKBACK_YEARS`` years ago.
        end_date: ``YYYY[-MM[-DD]]``; default (and maximum) is today.
        transform: How to transform raw values.
        max_points: Most recent observations kept per series.
        response_format: ``concise`` returns ``[period, value]`` pairs; ``detailed`` adds
            dates, footnotes and source notes.
        today: Override for tests.

    Returns:
        Results, per-series failures, and notes about limits or questionable transforms.
    """
    today = today or date.today()
    ids = normalize_ids(series_ids)
    start, end = resolve_window(start_date, end_date, today)
    # Transforms need a year of history before the window so the first points are not empty
    # (plus a week so weekly year-ago matching has a neighbour to snap to).
    fetch_start = start if transform == "level" else minus_years(start, 1) - timedelta(days=7)
    # A recent end_date often means "up to the latest" with a guessed month. Fetch through today
    # anyway (same BLS queries) so latest_available is known and cut-off data can be flagged.
    knows_latest = end >= minus_years(today, RECENT_END_YEARS)
    raw_by_id, failed = fetch_raw(ids, fetch_start, today if knows_latest else end, upstream)

    notes: list[str] = []
    limit = min(max_points, max(1, TOTAL_POINTS_LIMIT // max(len(raw_by_id), 1)))
    if limit < max_points:
        notes.append(
            f"Capped at {limit} points per series to keep the response under "
            f"{TOTAL_POINTS_LIMIT} points in total; request fewer series for longer histories."
        )

    results: list[SeriesData] = []
    for raw in (raw_by_id[s] for s in ids if s in raw_by_id):
        meta = meta_or_failure(raw, upstream, include_notes=response_format == "detailed")
        if isinstance(meta, FailedItem):
            failed.append(meta)
            continue
        if is_rate(meta.units) and transform.startswith("pct_change"):
            notes.append(
                f"{raw.series_id} is measured in {meta.units}; a percent change of a rate is "
                "rarely meaningful. Use change_year_ago or change_prior_period for "
                "percentage points."
            )
        data = _series_data(
            raw,
            meta,
            transform=transform,
            start=start,
            end=end,
            limit=limit,
            detailed=response_format == "detailed",
            knows_latest=knows_latest,
        )
        results.append(data)
        notes += _window_notes(raw, transform, start, end, data.latest_available)

    notes += stale_notes(raw_by_id.values())
    if any(r.truncated for r in results):
        notes.append(
            "Some series were truncated to their most recent observations (see 'truncated'). "
            "Pass a later start_date, or raise max_points (up to 500), for the full window."
        )
    return GetSeriesResponse(results=results, failed=failed, notes=notes, retrieved_at=_utc_now())


# ---------------------------------------------------------------- get_latest


def _change(new: float, old: float | None, *, pct: bool) -> float | None:
    """Absolute or percent change, rounded; ``None`` when there is nothing to compare."""
    if old is None:
        return None
    if pct:
        return None if old == 0 else round((new / old - 1) * 100, TRANSFORM_DECIMALS)
    return round(new - old, TRANSFORM_DECIMALS)


def _latest_value(raw: RawSeries, meta: SeriesMeta) -> LatestValue | FailedItem:
    """Latest, previous and year-ago values of one series."""
    present = RawSeries(raw.series_id, raw.source, raw.frame.loc[raw.values.notna()], raw.freq_code)
    if present.frame.empty:
        return FailedItem(
            id=raw.series_id,
            reason=f"No values in the last {LATEST_LOOKBACK_YEARS} years.",
            hint="The series may be discontinued; check observation_end with describe_series.",
        )
    rate = is_rate(meta.units)
    values = [float(v) for v in present.values.to_list()]
    dates = present.dates
    latest, previous = values[-1], (values[-2] if len(values) > 1 else None)
    year_ago_value = _to_optional_float(year_ago(present.values, raw.freq_code).iloc[-1])
    year_ago_label = period_label(minus_years(dates[-1], 1), raw.freq_code)
    notes = stale_notes([raw])
    if year_ago_value is None:
        # Real gaps exist: e.g. BLS published no October 2025 household-survey or CPI data
        # because of the federal government shutdown. Name the gap and its neighbours so a
        # fallback comparison is labelled correctly (DESIGN.md decision 41).
        notes.append(missing_year_ago_note(raw.values, minus_years(dates[-1], 1), raw.freq_code))
    return LatestValue(
        meta=meta,
        latest_period=period_label(dates[-1], raw.freq_code),
        latest_value=latest,
        preliminary=bool(present.frame["preliminary"].iloc[-1]),
        previous_period=period_label(dates[-2], raw.freq_code) if len(dates) > 1 else None,
        previous_value=previous,
        change_from_previous=_change(latest, previous, pct=False),
        pct_change_from_previous=None if rate else _change(latest, previous, pct=True),
        year_ago_period=year_ago_label,
        year_ago_value=year_ago_value,
        change_from_year_ago=_change(latest, year_ago_value, pct=False),
        pct_change_from_year_ago=None if rate else _change(latest, year_ago_value, pct=True),
        change_units="percentage points" if rate else meta.units,
        notes=notes,
    )


def get_latest(
    series_ids: list[str], upstream: Upstream, *, today: date | None = None
) -> GetLatestResponse:
    """Latest value of each series with prior-period and year-ago comparisons.

    Args:
        series_ids: 1+ BLS/FRED IDs (normalized here).
        upstream: Data access.
        today: Override for tests.

    Returns:
        One ``LatestValue`` per series with data, plus per-series failures.
    """
    today = today or date.today()
    ids = normalize_ids(series_ids)
    lookback_start = date(today.year - LATEST_LOOKBACK_YEARS, 1, 1)
    raw_by_id, failed = fetch_raw(ids, lookback_start, today, upstream)
    results: list[LatestValue] = []
    for raw in (raw_by_id[s] for s in ids if s in raw_by_id):
        meta = meta_or_failure(raw, upstream, include_notes=False)
        item = meta if isinstance(meta, FailedItem) else _latest_value(raw, meta)
        if isinstance(item, FailedItem):
            failed.append(item)
        else:
            results.append(item)
    return GetLatestResponse(results=results, failed=failed, retrieved_at=_utc_now())
