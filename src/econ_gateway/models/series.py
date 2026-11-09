"""Response models for the time-series tools (search, describe, latest, history).

These models *are* the tool output schemas the client model sees (FastMCP derives
``outputSchema`` and ``structuredContent`` from them), so field descriptions are written for an
LLM reader: what the field means, its units, and how to use it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["BLS", "FRED"]
Transform = Literal[
    "level",
    "pct_change_year_ago",
    "pct_change_prior_period",
    "change_year_ago",
    "change_prior_period",
]
ResponseFormat = Literal["concise", "detailed"]


class SeriesMeta(BaseModel):
    """What a series measures and where it comes from.

    Attributes:
        series_id: Official ID at the source; pass it to other tools.
        source: Publishing system (BLS API or FRED).
        title: Official title.
        units: Units of the raw values.
        frequency: Observation frequency in words.
        seasonal_adjustment: ``SA``, ``NSA``, ``SAAR`` etc., or ``unknown``.
        geography: Area covered, when known.
        url: Human-readable page for the series at the source.
        notes: Source notes (detailed format only).
        stale_as_of: Set only when the source could not be reached and the data is the
            gateway's cached copy: the UTC time that copy was fetched.
    """

    series_id: str
    source: Source
    title: str
    units: str = Field(
        description="Units of the raw values, e.g. 'percent' or 'index 1982-84=100'."
    )
    frequency: str = Field(description="e.g. 'monthly', 'quarterly', 'weekly'.")
    seasonal_adjustment: str = Field(description="SA = seasonally adjusted, NSA = not adjusted.")
    geography: str | None = None
    url: str = Field(description="Cite this link when quoting the data.")
    notes: str | None = None
    stale_as_of: str | None = Field(
        default=None,
        description=(
            "Null normally. When set, the source was unreachable and these values are the "
            "gateway's cached copy fetched at this UTC time; a newer release may exist, so say so."
        ),
    )


class Observation(BaseModel):
    """One data point (detailed format).

    Attributes:
        period: Readable period label: ``2026-09``, ``2026-Q3``, ``2026``, or a date (weekly).
        date: ISO date of the period start (week-ending date for weekly series).
        value: Value in the series units, or in the transform's units; ``None`` when unavailable.
        preliminary: True when the source marks the value preliminary (subject to revision).
        footnotes: Source footnotes for this observation.
    """

    period: str
    date: str
    value: float | None
    preliminary: bool = False
    footnotes: str | None = None


class SeriesData(BaseModel):
    """Observations for one series, possibly transformed and truncated.

    Attributes:
        meta: Series metadata and citation link.
        transform: Transform applied to the raw values.
        value_units: Units of the returned values after the transform.
        points: Concise format: ``[period, value]`` pairs, oldest first.
        observations: Detailed format: full observation records, oldest first.
        preliminary_periods: Periods the source marks preliminary.
        latest_available: Latest period with data at the source, regardless of the window asked for.
        returned: Number of observations returned.
        available: Observations in the requested window before truncation.
        truncated: True when older observations were dropped to respect size limits.
    """

    meta: SeriesMeta
    transform: Transform
    value_units: str
    points: list[tuple[str, float | None]] | None = None
    observations: list[Observation] | None = None
    preliminary_periods: list[str] = Field(default_factory=list)
    latest_available: str | None
    returned: int
    available: int
    truncated: bool


class FailedItem(BaseModel):
    """A requested item (series, area or occupation) that could not be returned.

    Attributes:
        id: The ID as requested.
        reason: What went wrong.
        hint: What to do next.
    """

    id: str
    reason: str
    hint: str


class GetSeriesResponse(BaseModel):
    """Result of ``get_series``.

    Attributes:
        results: One entry per series returned.
        failed: Requested series that could not be returned, with next steps.
        notes: Warnings about the request, e.g. size limits or a misleading transform.
        retrieved_at: UTC time the data was assembled.
    """

    results: list[SeriesData]
    failed: list[FailedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    retrieved_at: str


class LatestValue(BaseModel):
    """Most recent value of a series with standard comparisons.

    For rates (units in percent) the percent-change fields are ``None``: changes in a rate are
    reported in percentage points via the ``change_*`` fields.

    Attributes:
        meta: Series metadata and citation link.
        latest_period: Period of the latest available value.
        latest_value: Latest value, in the series units.
        preliminary: True when the latest value is preliminary.
        previous_period: The period before the latest one.
        previous_value: Value in the previous period.
        change_from_previous: ``latest - previous``, in units (percentage points for rates).
        pct_change_from_previous: Percent change from the previous period (``None`` for rates).
        year_ago_period: Same period one year earlier.
        year_ago_value: Value one year earlier.
        change_from_year_ago: ``latest - year_ago``, in units (percentage points for rates).
        pct_change_from_year_ago: Percent change over the year (``None`` for rates).
        change_units: Units of the ``change_*`` fields.
        notes: Explanations for missing comparisons, e.g. a period the agency never published.
    """

    meta: SeriesMeta
    latest_period: str
    latest_value: float
    preliminary: bool
    previous_period: str | None = None
    previous_value: float | None = None
    change_from_previous: float | None = None
    pct_change_from_previous: float | None = None
    year_ago_period: str | None = None
    year_ago_value: float | None = None
    change_from_year_ago: float | None = None
    pct_change_from_year_ago: float | None = None
    change_units: str
    notes: list[str] = Field(default_factory=list)


class GetLatestResponse(BaseModel):
    """Result of ``get_latest``.

    Attributes:
        results: One entry per series with data.
        failed: Requested series that could not be returned, with next steps.
        retrieved_at: UTC time the data was assembled.
    """

    results: list[LatestValue]
    failed: list[FailedItem] = Field(default_factory=list)
    retrieved_at: str


class SearchHit(BaseModel):
    """A candidate series for a search query.

    Attributes:
        series_id: Pass to ``get_latest`` / ``get_series`` / ``describe_series``.
        source: Publishing system.
        title: Official title.
        units: Units of the values.
        frequency: Observation frequency in words.
        seasonal_adjustment: ``SA`` / ``NSA`` etc.
        geography: Area covered, when known.
        latest_observation: Latest period with data, when known.
        match: ``catalog`` = curated and validated; ``fred_search`` = FRED full-text match.
    """

    series_id: str
    source: Source
    title: str
    units: str
    frequency: str
    seasonal_adjustment: str
    geography: str | None = None
    latest_observation: str | None = None
    match: Literal["catalog", "fred_search"]


class SearchResponse(BaseModel):
    """Result of ``search_series``.

    Attributes:
        query: The query as received.
        results: Candidates, best first.
        fred_searched: Whether FRED full-text search was used in addition to the catalog.
        notes: Why FRED was or was not searched, and other guidance.
    """

    query: str
    results: list[SearchHit]
    fred_searched: bool
    notes: list[str] = Field(default_factory=list)


class ReleaseInfo(BaseModel):
    """The agency release that publishes a series, and when it comes out.

    Attributes:
        release_id: FRED release ID (FRED's calendar covers BLS releases too).
        name: Release name, e.g. ``Employment Situation``; ``None`` if not in the gateway's list.
        last_release_date: Most recent release date on or before today (US Eastern).
        next_release_date: Next scheduled release date, when FRED has published it.
        url: FRED page for the release, with its full schedule.
    """

    release_id: int
    name: str | None = None
    last_release_date: str | None = Field(
        default=None, description="ISO date of the latest release on or before today."
    )
    next_release_date: str | None = Field(
        default=None,
        description=(
            "ISO date of the next scheduled release (most BLS releases are at 8:30 a.m. ET). "
            "Newer data than the latest period will not exist before then."
        ),
    )
    url: str


class DescribeResponse(BaseModel):
    """Result of ``describe_series``.

    Attributes:
        meta: Full metadata including source notes.
        observation_start: First period available at the source, when known.
        observation_end: Last period available at the source, when known.
        last_updated: When the source last updated the series, when known.
        in_catalog: True when the series is in the gateway's curated, validated catalog.
        release: The release that publishes the series and its last/next dates, when known.
    """

    meta: SeriesMeta
    observation_start: str | None = None
    observation_end: str | None = None
    last_updated: str | None = None
    in_catalog: bool
    release: ReleaseInfo | None = None
