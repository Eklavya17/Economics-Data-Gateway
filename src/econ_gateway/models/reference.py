"""Bodies of the ``econ://`` resources (Phase 5; served by ``resources.py``).

Clients attach resources as context rather than calling them, so these are plain JSON
documents. ``ReleaseInfo`` lives in ``series.py`` because ``describe_series`` returns it too.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from econ_gateway.models.series import (
    DescribeResponse,
    FailedItem,
    LatestValue,
    ReleaseInfo,
    Source,
)


class ReleaseCalendarResponse(BaseModel):
    """Body of ``econ://releases``: schedule of the headline releases the gateway serves.

    Attributes:
        as_of: Date (US Eastern) the schedule was computed for.
        releases: One entry per release, soonest next release first.
        notes: Caveats, e.g. no FRED key so no schedule.
    """

    as_of: str
    releases: list[ReleaseInfo]
    notes: list[str] = Field(default_factory=list)


class CatalogEntry(BaseModel):
    """One curated, validated series.

    Attributes:
        series_id: ID to pass to the data tools.
        source: Publishing system.
        title: Official title.
        category: Topic group (``unemployment``, ``wages``, ``inflation``...).
        frequency: Observation frequency in words.
        units: Units of the values.
        seasonal_adjustment: ``SA`` or ``NSA``.
        geography: Area covered; ``None`` means the United States (omitted to save space).
    """

    series_id: str
    source: Source
    title: str
    category: str
    frequency: str
    units: str
    seasonal_adjustment: str
    geography: str | None = None


class CatalogResponse(BaseModel):
    """Body of ``econ://catalog`` and ``econ://catalog/{category}``.

    Attributes:
        category: The category requested, or ``None`` for the whole catalog.
        count: Number of entries.
        categories: Every category with its number of series (to narrow the next read).
        entries: The series.
    """

    category: str | None = None
    count: int
    categories: dict[str, int]
    entries: list[CatalogEntry]


class SeriesResource(BaseModel):
    """Body of ``econ://series/{series_id}``: what a series is, plus its latest value.

    Attributes:
        description: Same as ``describe_series`` (metadata, coverage, release schedule).
        latest: Same as one ``get_latest`` result; ``None`` when no value could be fetched.
        failed: Why ``latest`` is missing, when it is.
        retrieved_at: UTC time the body was assembled.
    """

    description: DescribeResponse
    latest: LatestValue | None = None
    failed: list[FailedItem] = Field(default_factory=list)
    retrieved_at: str


class StatusResponse(BaseModel):
    """Body of ``econ://status``: configuration and health, never secrets.

    Attributes:
        version: Gateway version.
        toolsets: Mounted toolsets.
        bls_key_configured: Whether ``BLS_API_KEY`` is set (the key itself is never shown).
        fred_key_configured: Whether ``FRED_API_KEY`` is set.
        catalog_series: Series in the curated catalog.
        upstreams: Per upstream: circuit-breaker state, rate-limit headroom, BLS quota use.
        retrieved_at: UTC time of the snapshot.
    """

    version: str
    toolsets: list[str]
    bls_key_configured: bool
    fred_key_configured: bool
    catalog_series: int
    upstreams: dict[str, Any]
    retrieved_at: str
