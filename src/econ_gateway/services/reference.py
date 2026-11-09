"""Bodies of the data-backed ``econ://`` resources: catalog, one series, gateway status.

Resources are context a client attaches (or a user picks), not actions, so these functions
reuse the tool services rather than adding new data paths: the series resource is
``describe_series`` plus ``get_latest`` for one ID.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pandas as pd

from econ_gateway import __version__
from econ_gateway.consts import SERIES_ID_PATTERN
from econ_gateway.models import (
    CatalogEntry,
    CatalogResponse,
    SeriesResource,
    Source,
    StatusResponse,
)
from econ_gateway.services.describe import describe_series
from econ_gateway.services.errors import InvalidRequestError
from econ_gateway.services.metadata import frequency_words
from econ_gateway.services.observations import get_latest
from econ_gateway.services.upstream import Upstream


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(row: dict[str, str]) -> CatalogEntry:
    source: Source = "BLS" if row["source"] == "BLS" else "FRED"
    return CatalogEntry(
        series_id=row["series_id"],
        source=source,
        title=row["title"],
        category=row["category"],
        frequency=frequency_words(row["frequency"]),
        units=row["units"],
        seasonal_adjustment=row["seasonal_adjustment"],
        geography=None if row["geography"] in ("", "United States") else row["geography"],
    )


def catalog_listing(upstream: Upstream, category: str | None = None) -> CatalogResponse:
    """The curated catalog, optionally one category, ordered by category then ID.

    Args:
        upstream: Data access (only the local catalog is read; no network).
        category: Category name such as ``unemployment``; ``None`` for everything.

    Raises:
        InvalidRequestError: For an unknown category; the message lists the valid ones.
    """
    catalog = upstream.catalog().fillna("")
    counts = {str(k): int(v) for k, v in catalog["category"].value_counts().sort_index().items()}
    if category is not None:
        if category not in counts:
            raise InvalidRequestError(
                f"Unknown catalog category '{category}'. Valid categories: "
                f"{', '.join(counts)}. Read econ://catalog for everything."
            )
        catalog = catalog.loc[catalog["category"] == category]
    ordered = pd.DataFrame(catalog).sort_values(by=["category", "series_id"])
    entries = [_entry(row) for row in ordered.astype(str).to_dict(orient="records")]
    return CatalogResponse(
        category=category, count=len(entries), categories=counts, entries=entries
    )


def series_resource(
    series_id: str, upstream: Upstream, *, today: date | None = None
) -> SeriesResource:
    """What a series is (``describe_series``) plus its latest value (``get_latest``).

    Args:
        series_id: BLS or FRED ID.
        upstream: Data access.
        today: Override for tests.

    Raises:
        InvalidRequestError: When the ID is malformed.
        SeriesNotFoundError: When neither the catalog nor the source knows the ID.
        UpstreamError: When the source cannot be reached for the description.
    """
    sid = series_id.strip().upper()
    if not re.fullmatch(SERIES_ID_PATTERN, sid):
        raise InvalidRequestError(
            f"'{series_id}' is not a valid series ID. Use an ID from econ://catalog or "
            "search_series, e.g. econ://series/LNS14000000."
        )
    description = describe_series(sid, upstream, today=today)
    latest = get_latest([sid], upstream, today=today)
    return SeriesResource(
        description=description,
        latest=latest.results[0] if latest.results else None,
        failed=latest.failed,
        retrieved_at=_utc_now(),
    )


def gateway_status(upstream: Upstream, toolsets: list[str]) -> StatusResponse:
    """Configuration and per-upstream health. Reports whether keys are set, never their values."""
    return StatusResponse(
        version=__version__,
        toolsets=toolsets,
        bls_key_configured=upstream.has_bls_key,
        fred_key_configured=upstream.has_fred_key,
        catalog_series=len(upstream.catalog()),
        upstreams=upstream.status(),
        retrieved_at=_utc_now(),
    )
