"""Compute toolset: server-side arithmetic for common multi-step questions.

Deterministic numbers only, no narrative (DESIGN.md decisions 12 and 18). Both tools take
series IDs from ``search_series`` (core toolset).
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from econ_gateway import services
from econ_gateway.consts import (
    COMPARE_MAX_SERIES,
    DATE_PATTERN,
    MAX_POINTS_DEFAULT,
    MAX_POINTS_LIMIT,
    SERIES_ID_PATTERN,
    TOOL_TIMEOUT_S,
)
from econ_gateway.models import CompareMeasure, CompareResponse, Deflator, RealWageResponse
from econ_gateway.services.upstream import Upstream
from econ_gateway.toolsets._common import READ_ONLY_EXTERNAL, tool_errors

SeriesId = Annotated[str, Field(pattern=SERIES_ID_PATTERN)]


def build(upstream: Upstream) -> FastMCP:
    """Create the compute toolset as a FastMCP sub-server bound to ``upstream``."""
    mcp: FastMCP = FastMCP("compute", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def real_wage_growth(
        wage_series_id: Annotated[
            SeriesId,
            Field(
                description=(
                    "A wage LEVEL series from search_series: average hourly/weekly earnings "
                    "(e.g. CES3000000003 manufacturing) or an Employment Cost Index "
                    "(e.g. CIU2020000000000I). Not a rate or a percent-change series."
                )
            ),
        ],
        deflator: Annotated[
            Deflator,
            Field(
                description=(
                    "'cpi_u' (all urban consumers, the usual choice), 'cpi_w' (urban wage "
                    "earners, used for Social Security), or 'pce' (the Fed's preferred index)."
                )
            ),
        ] = "cpi_u",
        start_date: Annotated[
            str | None,
            Field(pattern=DATE_PATTERN, description="Window start, e.g. '2019' or '2020-02'."),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(pattern=DATE_PATTERN, description="Window end. Default: latest."),
        ] = None,
        max_points: Annotated[
            int,
            Field(ge=1, le=MAX_POINTS_LIMIT, description="Most recent periods in `points`."),
        ] = MAX_POINTS_DEFAULT,
    ) -> RealWageResponse:
        """Are wages keeping up with inflation? Inflation-adjusted growth of a wage series.

        ## Usage
        For "real wages", "purchasing power" and "are wages keeping up with prices"
        questions. Find the wage series with `search_series` first ("average hourly
        earnings manufacturing", "employment cost index wages").

        ## Tips
        - `latest` gives the year-over-year nominal, inflation and real growth for the most
          recent period; `window` gives the cumulative change since `start_date`. Use
          `start_date` for "since 2019" or "since the pandemic" questions.
        - The price index is matched to the wage series' seasonal adjustment and frequency
          automatically; `notes` says what was done.
        - Real growth is exact, (1 + wage growth) / (1 + inflation) - 1, so it can differ from
          a simple subtraction by a few hundredths.

        ## Output format
        `wage`, `deflator` (metadata with citation urls), `frequency`, `base_period`,
        `latest` (period, nominal_wage, nominal/inflation/real pct change year ago, real_wage),
        `real_wage_rising`, `window` (cumulative changes), `points`, `truncated`, `notes`.

        IMPORTANT: Cite both `wage.url` and `deflator.url`, because the answer depends on both
        series.
        """
        with tool_errors():
            return services.real_wage_growth(
                wage_series_id,
                upstream,
                deflator=deflator,
                start_date=start_date,
                end_date=end_date,
                max_points=max_points,
            )

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def compare_series(
        series_ids: Annotated[
            list[SeriesId],
            Field(
                min_length=2,
                max_length=COMPARE_MAX_SERIES,
                description="2-25 series IDs from search_series to rank against each other.",
            ),
        ],
        measure: Annotated[
            CompareMeasure,
            Field(
                description=(
                    "'latest_value'; 'pct_change_year_ago' / 'change_year_ago' (vs same period "
                    "a year earlier); 'pct_change_since' / 'change_since' (vs `since`). Use "
                    "change_* for rates (percentage points)."
                )
            ),
        ],
        since: Annotated[
            str | None,
            Field(
                pattern=DATE_PATTERN,
                description=(
                    "Required for *_since. 'YYYY' compares with that year's average; "
                    "'YYYY-MM' with that month."
                ),
            ),
        ] = None,
        benchmark_series_id: Annotated[
            SeriesId | None,
            Field(
                description=(
                    "Optional series computed the same way and subtracted from each row, "
                    "e.g. 'CUSR0000SA0' (CPI) to compare wage growth with inflation."
                )
            ),
        ] = None,
    ) -> CompareResponse:
        """Rank several series on one measure, optionally against a benchmark such as CPI.

        ## Usage
        For "which industries/states/groups had the biggest ...", "rank ...", and "compare A,
        B and C" questions. Get the IDs from `search_series` (e.g. average hourly earnings for
        each industry), then rank them here instead of fetching each and sorting yourself.

        ## Tips
        - Percent measures compare across different units; level and change measures only
          make sense for series in the same units (`notes` warns otherwise).
        - Rates (unemployment, quits) must use change_* measures; percent changes of rates are
          left out with a note.
        - `gap_vs_benchmark` > 0 means the series grew faster than the benchmark.

        ## Output format
        `rows` ranked highest first: meta, rank, value, value_units, latest_period,
        latest_value, base_period, base_value, gap_vs_benchmark. `benchmark`, `failed`,
        `notes` (comparability warnings).

        IMPORTANT: Read `notes` before answering, because they flag series that end in
        different periods or are in incomparable units.
        """
        with tool_errors():
            return services.compare_series(
                series_ids,
                upstream,
                measure=measure,
                since=since,
                benchmark_series_id=benchmark_series_id,
            )

    return mcp
