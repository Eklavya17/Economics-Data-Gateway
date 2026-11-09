"""Inflation-adjusted wage growth (``real_wage_growth``).

Method (DESIGN.md decision 19):

- **Deflator variant follows the wage's seasonal adjustment.** SA wages are paired with the SA
  CPI, NSA wages (state series, ECI) with the NSA CPI. PCE is published SA only.
- **Frequencies are aligned** by averaging the monthly price index over complete quarters or
  years when the wage series is quarterly or annual.
- **Real growth is exact:** ``(1 + nominal) / (1 + inflation) - 1``.
- **Real levels** are expressed in prices of the latest period with both series.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from datetime import date
from typing import Any

import pandas as pd

from econ_gateway.consts import TRANSFORM_DECIMALS
from econ_gateway.models import (
    Deflator,
    RealWagePoint,
    RealWageResponse,
    SeriesMeta,
    WindowChange,
)
from econ_gateway.services.errors import (
    DataNotAvailableError,
    InvalidRequestError,
    SeriesNotFoundError,
)
from econ_gateway.services.metadata import frequency_words, is_rate, lookup_meta
from econ_gateway.services.observations import (
    RawSeries,
    fetch_raw,
    resolve_window,
    stale_notes,
    with_staleness,
)
from econ_gateway.services.transforms import as_datetimes, period_label, to_frequency, year_ago
from econ_gateway.services.upstream import Upstream

# deflator -> (seasonally adjusted ID, not seasonally adjusted ID or None)
DEFLATOR_IDS: dict[str, tuple[str, str | None]] = {
    "cpi_u": ("CUSR0000SA0", "CUUR0000SA0"),  # CPI for all urban consumers, all items
    "cpi_w": ("CWSR0000SA0", "CWUR0000SA0"),  # CPI for urban wage earners and clerical workers
    "pce": ("PCEPI", None),  # PCE price index (FRED), SA only
}
_SUPPORTED_WAGE_FREQS = {"M", "Q", "A"}


def _fetch_one(series_id: str, start: date, end: date, upstream: Upstream) -> RawSeries:
    """Fetch a single series or raise with the reason the source gave."""
    found, failed = fetch_raw([series_id], start, end, upstream)
    if series_id not in found:
        reason = failed[0].reason if failed else "No data returned."
        raise SeriesNotFoundError(f"{series_id}: {reason} Call search_series to find a valid ID.")
    return found[series_id]


def _pick_deflator(deflator: Deflator, wage: SeriesMeta) -> tuple[str, list[str]]:
    """Deflator series ID matching the wage's seasonal adjustment, with an explanatory note."""
    sa_id, nsa_id = DEFLATOR_IDS[deflator]
    wage_is_sa = wage.seasonal_adjustment.upper().startswith("SA")
    if wage_is_sa or nsa_id is None:
        notes = []
        if not wage_is_sa:
            notes.append(
                "PCE is only published seasonally adjusted while this wage series is not; "
                "year-over-year growth is still comparable, real levels mix adjusted and "
                "unadjusted data."
            )
        return sa_id, notes
    return nsa_id, [
        f"The wage series is not seasonally adjusted, so the unadjusted price index {nsa_id} "
        "is used to match it."
    ]


def _pct(new: pd.Series, old: pd.Series) -> pd.Series:
    return (new / old - 1) * 100


def _rounded(record: Mapping[Hashable, Any], column: str) -> float | None:
    value = record[column]
    return None if value is None or pd.isna(value) else round(float(value), TRANSFORM_DECIMALS)


def _point(period: str, record: Mapping[Hashable, Any]) -> RealWagePoint:
    return RealWagePoint(
        period=period,
        nominal_wage=_rounded(record, "wage"),
        nominal_pct_change_year_ago=_rounded(record, "nominal"),
        inflation_pct_change_year_ago=_rounded(record, "inflation"),
        real_pct_change_year_ago=_rounded(record, "real"),
        real_wage=_rounded(record, "real_wage"),
    )


def _window_change(records: list[dict[Hashable, Any]], labels: list[str]) -> WindowChange | None:
    """Cumulative nominal, price and real change from the first to the last period."""
    if len(records) < 2:
        return None
    first, last = records[0], records[-1]
    nominal = float(last["wage"]) / float(first["wage"]) - 1
    inflation = float(last["price"]) / float(first["price"]) - 1
    return WindowChange(
        start_period=labels[0],
        end_period=labels[-1],
        nominal_pct_change=round(nominal * 100, TRANSFORM_DECIMALS),
        inflation_pct_change=round(inflation * 100, TRANSFORM_DECIMALS),
        real_pct_change=round(((1 + nominal) / (1 + inflation) - 1) * 100, TRANSFORM_DECIMALS),
    )


def real_wage_growth(
    wage_series_id: str,
    upstream: Upstream,
    *,
    deflator: Deflator = "cpi_u",
    start_date: str | None = None,
    end_date: str | None = None,
    max_points: int,
    today: date | None = None,
) -> RealWageResponse:
    """Nominal, inflation and real (inflation-adjusted) growth of a wage series.

    Args:
        wage_series_id: A wage *level* series (e.g. average hourly earnings, an ECI index).
        upstream: Data access.
        deflator: Price index to deflate by.
        start_date: First period of the window (``YYYY[-MM[-DD]]``); default 5 years ago.
        end_date: Last period; default latest.
        max_points: Most recent periods returned in ``points``.
        today: Override for tests.

    Returns:
        Latest growth rates, change over the window, and per-period values.

    Raises:
        SeriesNotFoundError: When the wage or price series returns no data.
        InvalidRequestError: When the wage series is a rate or has an unsupported frequency.
        DataNotAvailableError: When the two series have no periods in common.
    """
    today = today or date.today()
    start, end = resolve_window(start_date, end_date, today)
    # A full prior year is needed for the first year-over-year value and complete-period means.
    fetch_start = date(start.year - 1, 1, 1)
    wage = _fetch_one(wage_series_id.strip().upper(), fetch_start, end, upstream)
    wage_meta = with_staleness(
        lookup_meta(wage.series_id, wage.source, upstream, frequency_code=wage.freq_code), wage
    )
    if is_rate(wage_meta.units):
        raise InvalidRequestError(
            f"{wage.series_id} is measured in {wage_meta.units}, not a wage level. Use a wage "
            "level series such as average hourly earnings (e.g. CES0500000003) or an Employment "
            "Cost Index (e.g. CIU2020000000000I)."
        )
    if wage.freq_code not in _SUPPORTED_WAGE_FREQS:
        raise InvalidRequestError(
            f"{wage.series_id} is {frequency_words(wage.freq_code)}; real wages are computed "
            "for monthly, quarterly or annual wage series."
        )

    price_id, notes = _pick_deflator(deflator, wage_meta)
    price = _fetch_one(price_id, fetch_start, end, upstream)
    price_meta = with_staleness(
        lookup_meta(price.series_id, price.source, upstream, frequency_code=price.freq_code),
        price,
    )
    notes += stale_notes([wage, price])
    prices = to_frequency(price.values, price.freq_code, wage.freq_code)
    if prices is None:
        raise InvalidRequestError(
            f"Cannot align {frequency_words(price.freq_code)} prices with "
            f"{frequency_words(wage.freq_code)} wages."
        )
    if price.freq_code != wage.freq_code:
        notes.append(
            f"{frequency_words(price.freq_code).capitalize()} prices were averaged to "
            f"{frequency_words(wage.freq_code)} (complete periods only) to match the wages."
        )

    wages = wage.values.dropna()
    frame = pd.DataFrame({"wage": wages, "price": prices}).dropna()
    if frame.empty:
        raise DataNotAvailableError(
            f"{wage.series_id} and {price_id} have no periods in common in this window."
        )
    wage_s, price_s = pd.Series(frame["wage"]), pd.Series(frame["price"])
    nominal = _pct(wage_s, year_ago(wage_s, wage.freq_code))
    inflation = _pct(price_s, year_ago(price_s, wage.freq_code))
    frame = frame.assign(
        nominal=nominal,
        inflation=inflation,
        real=((1 + nominal / 100) / (1 + inflation / 100) - 1) * 100,
        real_wage=wage_s * float(price_s.iloc[-1]) / price_s,
    ).loc[pd.Timestamp(start) :]

    labels = [period_label(day, wage.freq_code) for day in as_datetimes(frame.index)]
    records = frame.to_dict(orient="records")
    points = [_point(label, rec) for label, rec in zip(labels, records, strict=True)]
    complete = [p for p in points if p.real_pct_change_year_ago is not None]
    latest = complete[-1] if complete else None
    wage_latest = period_label(as_datetimes(wages.index)[-1], wage.freq_code)
    if labels and wage_latest != labels[-1]:
        notes.append(
            f"Wages are published through {wage_latest} but prices are not yet; results end "
            f"at {labels[-1]}."
        )
    return RealWageResponse(
        wage=wage_meta,
        deflator=price_meta,
        frequency=frequency_words(wage.freq_code),
        base_period=labels[-1] if labels else "n/a",
        latest=latest,
        real_wage_rising=None if latest is None else (latest.real_pct_change_year_ago or 0) > 0,
        window=_window_change(records, labels),
        points=points[-max_points:],
        truncated=len(points) > max_points,
        notes=notes,
    )
