"""Core toolset: discover series and places, describe series, and fetch their data.

Typical flow for the client model: ``search_series`` → ``get_latest`` (headline numbers) or
``get_series`` (history and trends). ``describe_series`` answers "what exactly is this series?".
``find_area`` turns a place name into an ``area_id`` (used by the wages and regional toolsets)
and gives the place's unemployment series IDs directly.

Tools are plain (sync) functions. FastMCP runs them in a worker thread (``run_in_thread=True``
by default), so the blocking HTTP clients never stall the event loop.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from econ_gateway import services
from econ_gateway.consts import (
    DATE_PATTERN,
    FIND_DEFAULT_LIMIT,
    FIND_MAX_LIMIT,
    GET_LATEST_MAX_IDS,
    GET_SERIES_MAX_IDS,
    MAX_POINTS_DEFAULT,
    MAX_POINTS_LIMIT,
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    SERIES_ID_PATTERN,
    TOOL_TIMEOUT_S,
)
from econ_gateway.models import (
    AreaLevel,
    DescribeResponse,
    FindAreaResponse,
    GetLatestResponse,
    GetSeriesResponse,
    ResponseFormat,
    SearchResponse,
    Transform,
)
from econ_gateway.services.upstream import Upstream
from econ_gateway.toolsets._common import READ_ONLY_EXTERNAL, tool_errors

SeriesId = Annotated[str, Field(pattern=SERIES_ID_PATTERN)]


def build(upstream: Upstream) -> FastMCP:
    """Create the core toolset as a FastMCP sub-server bound to ``upstream``.

    Args:
        upstream: Data access; tests pass an in-memory fake.

    Returns:
        A server to ``mount`` on the main gateway.
    """
    mcp: FastMCP = FastMCP("core", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def search_series(
        query: Annotated[
            str,
            Field(
                min_length=2,
                max_length=200,
                description=(
                    "What to measure, in plain words: 'unemployment rate', 'average hourly "
                    "earnings manufacturing', 'job openings', 'CPI shelter', 'initial jobless "
                    "claims', '30-year mortgage rate'."
                ),
            ),
        ],
        geography: Annotated[
            str | None,
            Field(
                max_length=100,
                description=(
                    "Optional place: a state ('Texas'), metro ('Chicago'), or census region "
                    "('South'). Omit for national data."
                ),
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=SEARCH_MAX_LIMIT)] = SEARCH_DEFAULT_LIMIT,
    ) -> SearchResponse:
        """Find official BLS/FRED series IDs for a topic and place. Start here.

        ## Usage
        Call this first whenever you do not already have a series ID, then pass the chosen
        `series_id` to `get_latest` (current value) or `get_series` (history).
        Covers employment and payrolls (CES), unemployment and labor force (CPS, LAUS for
        states), wages and hours, prices (CPI by item and metro), job openings, hires and
        quits (JOLTS), employment cost (ECI), productivity, and FRED macro series (GDP,
        interest rates, mortgage rates, jobless claims).

        ## Tips
        - Put the measure in `query` and the place in `geography`.
        - Prefer `match: "catalog"` results: curated and validated. `fred_search` results are
          FRED full-text matches; check the title and units before using them.
        - Seasonally adjusted (SA) series are best for month-to-month comparisons; NSA for
          comparing the same month across years.

        ## Output format
        `results` best first, each with series_id, title, units, frequency,
        seasonal_adjustment, geography, latest_observation and match. `notes` explains
        whether FRED was also searched.

        IMPORTANT: Never guess or construct series IDs yourself, because made-up IDs return
        wrong or no data. Always take IDs from this tool's results.
        """
        with tool_errors():
            return services.search_series(query, upstream, geography=geography, limit=limit)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def describe_series(
        series_id: Annotated[
            SeriesId,
            Field(description="A BLS or FRED series ID, e.g. 'LNS14000000' or 'UNRATE'."),
        ],
    ) -> DescribeResponse:
        """Explain exactly what a series measures: title, units, frequency, coverage and notes.

        ## Usage
        Use when you need to confirm a series is the right one (definition, units, seasonal
        adjustment, date coverage) before quoting it, or to check if it is discontinued.

        ## Output format
        `meta` (title, units, frequency, seasonal_adjustment, geography, url, notes),
        `observation_start` / `observation_end` / `last_updated` when the source provides
        them, and `in_catalog` (true = curated and validated by this server).
        """
        with tool_errors():
            return services.describe_series(series_id, upstream)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def find_area(
        query: Annotated[
            str,
            Field(
                min_length=2,
                max_length=100,
                description=(
                    "Place name or state abbreviation: 'Texas', 'TX', 'Austin', 'Travis County', "
                    "'Chicago', 'United States'."
                ),
            ),
        ],
        level: Annotated[
            AreaLevel | None,
            Field(description="Restrict to 'national', 'state', 'metro' or 'county'."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=FIND_MAX_LIMIT)] = FIND_DEFAULT_LIMIT,
    ) -> FindAreaResponse:
        """Turn a place name into an area_id and its local unemployment series IDs.

        ## Usage
        Call before `get_occupation_wages` or `get_area_employment` (they take `area_id`).
        Also the fastest route to local unemployment: take `laus.unemployment_rate` from the
        result and pass it to `get_latest` or `get_series`.

        ## Tips
        - Metros are named by their principal cities ("Austin-Round Rock-San Marcos, TX");
          counties and metros can share names (Austin County, TX vs the Austin metro), so check
          `level`.
        - `has_occupation_wages` is false for counties (OEWS does not publish them); use the metro.
        - Metro and county unemployment series are not seasonally adjusted: compare with the
          same month a year earlier.

        ## Output format
        `results` best first: area_id (us, state:48, metro:12420, county:48453), name, level,
        has_occupation_wages, has_area_employment, laus (series IDs), score. `notes` flags
        ambiguous matches.

        IMPORTANT: If `notes` says several places match closely, confirm with the user which one
        they mean, because answering for the wrong place is worse than asking.
        """
        with tool_errors():
            return services.find_area(query, upstream, level=level, limit=limit)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def get_latest(
        series_ids: Annotated[
            list[SeriesId],
            Field(
                min_length=1,
                max_length=GET_LATEST_MAX_IDS,
                description="1-20 series IDs from search_series, e.g. ['LNS14000000', 'CPIAUCSL'].",
            ),
        ],
    ) -> GetLatestResponse:
        """Get the most recent published value of each series, with standard comparisons.

        ## Usage
        Best tool for "what is the current ..." questions. For trends or charts over time,
        use `get_series` instead.

        ## Tips
        - For rates (units in percent) changes are in percentage points and the percent-change
          fields are null on purpose.
        - `latest_period` is the latest data the agency has published, which may be one or
          two months behind today; say so when answering.
        - For prices, `pct_change_from_year_ago` on an index (e.g. CPI) is the inflation rate.
        - Also the best tool for "how has X changed over the past year": the year-ago fields
          give the change for the latest period.
        - A null year-ago value means the agency never published that period (e.g. October
          2025). `notes` names it and the nearest published periods.

        ## Output format
        Per series: `meta` (with citation url), latest_period, latest_value, preliminary,
        previous_period/value, change_from_previous, pct_change_from_previous,
        year_ago_period/value, change_from_year_ago, pct_change_from_year_ago, change_units,
        notes. `failed` lists IDs that returned nothing, with a next step.

        IMPORTANT: Quote the period and cite `meta.url` with each number, because readers need
        to know which release the figure comes from. If a comparison period was not published,
        say so, and never present a neighbouring period's value under its label.
        """
        with tool_errors():
            return services.get_latest(series_ids, upstream)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def get_series(
        series_ids: Annotated[
            list[SeriesId],
            Field(
                min_length=1,
                max_length=GET_SERIES_MAX_IDS,
                description="1-10 series IDs from search_series.",
            ),
        ],
        start_date: Annotated[
            str | None,
            Field(
                pattern=DATE_PATTERN,
                description="Start: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'. Default: 5 years ago.",
            ),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(
                pattern=DATE_PATTERN,
                description="Last period, same formats. Default: latest available.",
            ),
        ] = None,
        transform: Annotated[
            Transform,
            Field(
                description=(
                    "'level' = values as published; 'pct_change_year_ago' = % change vs the "
                    "same period a year earlier (inflation from a price index, wage growth); "
                    "'pct_change_prior_period' = % change vs previous period; "
                    "'change_year_ago' / 'change_prior_period' = difference in units "
                    "(use these for rates: percentage points)."
                )
            ),
        ] = "level",
        max_points: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_POINTS_LIMIT,
                description="Most recent observations kept per series.",
            ),
        ] = MAX_POINTS_DEFAULT,
        response_format: Annotated[
            ResponseFormat,
            Field(
                description=(
                    "'concise' = [period, value] pairs (default, fewer tokens); 'detailed' adds "
                    "dates, per-observation footnotes and source notes."
                )
            ),
        ] = "concise",
    ) -> GetSeriesResponse:
        """Get historical observations for one or more series, optionally transformed.

        ## Usage
        Use for trends, comparisons over time, and "since <year>" questions. For a single
        current value use `get_latest`. Several IDs are fetched in one call; compare series
        with the same units and frequency.

        ## Tips
        - Do the arithmetic here with `transform` instead of computing it yourself.
        - Omit `end_date` for current data. Never guess the latest month: it is in
          `latest_available`, and `notes` says when `end_date` cut off newer data.
        - A null point means the agency never published that period; `notes` names such gaps.
        - Values marked in `preliminary_periods` are subject to revision.

        ## Output format
        Per series: `meta`, `transform`, `value_units`, `points` ([period, value], oldest
        first) or `observations` (detailed), `latest_available`, `returned`, `available`,
        `truncated`. `failed` lists IDs that returned nothing; `notes` explains caps,
        questionable transforms, unpublished periods and data cut off by `end_date`.

        IMPORTANT: If `truncated` is true you are missing older data; narrow `start_date` or
        raise `max_points` rather than describing the shortened window as the full history.
        """
        with tool_errors():
            return services.get_series(
                series_ids,
                upstream,
                start_date=start_date,
                end_date=end_date,
                transform=transform,
                max_points=max_points,
                response_format=response_format,
            )

    return mcp
