"""Full metadata for one series (``describe_series``), including its release schedule."""

from __future__ import annotations

from datetime import date

import pandas as pd

from econ_gateway.models import DescribeResponse, Source
from econ_gateway.services.errors import SeriesNotFoundError
from econ_gateway.services.metadata import (
    catalog_row,
    meta_from_bls,
    meta_from_catalog,
    meta_from_fred,
)
from econ_gateway.services.releases import series_release, today_eastern
from econ_gateway.services.transforms import infer_freq_code, period_label
from econ_gateway.services.upstream import Upstream


def _not_found(series_id: str) -> SeriesNotFoundError:
    return SeriesNotFoundError(
        f"No BLS or FRED series '{series_id}' was found. Call search_series with a description "
        "of the data (e.g. 'unemployment rate Texas') to get a valid ID."
    )


def describe_series(
    series_id: str, upstream: Upstream, *, today: date | None = None
) -> DescribeResponse:
    """Metadata, coverage, source notes and release schedule for one series.

    Args:
        series_id: BLS or FRED ID (case-insensitive).
        upstream: Data access.
        today: Override for tests.

    Returns:
        The description.

    Raises:
        SeriesNotFoundError: When neither the catalog nor the source knows the ID.
        UpstreamError: When the source cannot be reached.
    """
    sid = series_id.strip().upper()
    source = upstream.infer_source(sid)
    response = _describe(sid, source, upstream, today or date.today())
    # The schedule is advisory: a calendar failure yields None rather than an error.
    response.release = series_release(sid, source, upstream, today or today_eastern())
    return response


def _describe(sid: str, source: Source, upstream: Upstream, today: date) -> DescribeResponse:
    """Metadata and coverage, from the catalog, FRED or BLS depending on the series."""
    row = catalog_row(sid, upstream.catalog())

    if source == "FRED":
        info = upstream.fred_series_info(sid)  # raises SeriesNotFoundError for unknown IDs
        meta = meta_from_fred(info, include_notes=True)
        if row is not None:  # keep curated facets (geography, normalized units) from the catalog
            curated = meta_from_catalog(row)
            curated.notes = meta.notes
            meta = curated
        return DescribeResponse(
            meta=meta,
            observation_start=info.get("observation_start"),
            observation_end=info.get("observation_end"),
            last_updated=info.get("last_updated"),
            in_catalog=row is not None,
        )

    if row is not None:
        return DescribeResponse(
            meta=meta_from_catalog(row),
            observation_end=row.get("last_obs") or None,
            in_catalog=True,
        )

    # BLS series outside the catalog: confirm it exists with a one-year fetch, which also
    # gives its frequency and latest period; add titles from BLS metadata when a key is set.
    frame, _ = upstream.fetch_bls([sid], today.year - 1, today.year)
    part = frame.loc[(frame["series_id"] == sid) & frame["value"].notna()]
    if part.empty:
        raise _not_found(sid)
    dates = pd.DatetimeIndex(part["date"])
    freq_code = infer_freq_code(dates)
    return DescribeResponse(
        meta=meta_from_bls(sid, upstream.bls_series_info(sid), freq_code),
        observation_end=period_label(max(ts.to_pydatetime() for ts in dates), freq_code),
        in_catalog=False,
    )
