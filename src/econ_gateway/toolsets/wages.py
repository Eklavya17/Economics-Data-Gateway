"""Wages toolset: occupations and OEWS wages by place.

Flow for the client model: ``find_occupation`` (SOC code) + ``find_area`` (area_id, core
toolset) → ``get_occupation_wages``. Ranking questions ("which metros pay nurses most?") skip
``find_area`` and use ``rank_across``.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from econ_gateway import services
from econ_gateway.consts import (
    AREA_ID_PATTERN,
    FIND_DEFAULT_LIMIT,
    FIND_MAX_LIMIT,
    SOC_CODE_PATTERN,
    TOOL_TIMEOUT_S,
    WAGES_MAX_AREAS,
    WAGES_MAX_OCCUPATIONS,
    WAGES_RANK_DEFAULT_LIMIT,
    WAGES_RANK_MAX_LIMIT,
)
from econ_gateway.models import (
    FindOccupationResponse,
    OccupationWagesResponse,
    ResponseFormat,
    WageMeasure,
)
from econ_gateway.services.upstream import Upstream
from econ_gateway.services.wages import RankScope
from econ_gateway.toolsets._common import READ_ONLY_EXTERNAL, tool_errors

SocCode = Annotated[str, Field(pattern=SOC_CODE_PATTERN)]
AreaId = Annotated[str, Field(pattern=AREA_ID_PATTERN)]


def build(upstream: Upstream) -> FastMCP:
    """Create the wages toolset as a FastMCP sub-server bound to ``upstream``."""
    mcp: FastMCP = FastMCP("wages", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def find_occupation(
        query: Annotated[
            str,
            Field(
                min_length=2,
                max_length=100,
                description=(
                    "Job title or field in plain words ('registered nurse', 'software developer', "
                    "'truck driver', 'healthcare'), or a SOC code ('29-1141')."
                ),
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=FIND_MAX_LIMIT)] = FIND_DEFAULT_LIMIT,
    ) -> FindOccupationResponse:
        """Find Standard Occupational Classification (SOC) codes for a job title.

        ## Usage
        Call before `get_occupation_wages`, which takes SOC codes.

        ## Tips
        - `group` tells you the level: `detailed` is one occupation (Registered Nurses);
          `major` is a whole family (Healthcare Practitioners and Technical Occupations).
          Pick the level the question is about.
        - "Tech" or "healthcare" questions usually mean the major group (15-0000, 29-0000).

        ## Output format
        `results` best first: occupation_code, title, group, score.
        """
        with tool_errors():
            return services.find_occupation(query, upstream, limit=limit)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def get_occupation_wages(
        occupation_codes: Annotated[
            list[SocCode],
            Field(
                min_length=1,
                max_length=WAGES_MAX_OCCUPATIONS,
                description="1-5 SOC codes from find_occupation, e.g. ['29-1141'].",
            ),
        ],
        area_ids: Annotated[
            list[AreaId] | None,
            Field(
                max_length=WAGES_MAX_AREAS,
                description=(
                    "Specific places from find_area, e.g. ['state:48', 'metro:12420']. Omit, "
                    "with no rank_across, for national figures only."
                ),
            ),
        ] = None,
        rank_across: Annotated[
            RankScope | None,
            Field(description="Rank every 'states' or 'metros' instead of listing area_ids."),
        ] = None,
        sort_by: Annotated[
            WageMeasure,
            Field(description="Measure used to rank when rank_across is set."),
        ] = "annual_median",
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=WAGES_RANK_MAX_LIMIT,
                description="Top areas kept per occupation when ranking.",
            ),
        ] = WAGES_RANK_DEFAULT_LIMIT,
        year: Annotated[
            int | None,
            Field(
                ge=2015,
                le=2100,
                description="OEWS May reference year. Omit for the latest published.",
            ),
        ] = None,
        response_format: Annotated[
            ResponseFormat,
            Field(description="'detailed' adds 25th and 75th percentile annual wages."),
        ] = "concise",
    ) -> OccupationWagesResponse:
        """Get official employment and wages for occupations by state or metro (BLS OEWS).

        ## Usage
        - Specific places: `occupation_codes` + `area_ids` (from `find_area`).
        - Rankings ("highest-paying metros for nurses"): `occupation_codes` + `rank_across`.
        - National only: `occupation_codes` alone.
        National figures for the same occupations are always included in `national`, so
        comparisons with the U.S. need no extra call.

        ## Tips
        - Median is the typical worker; mean is pulled up by high earners. Prefer median
          unless asked for average.
        - Figures are for May of `reference_period`, the latest OEWS survey; say so (e.g.
          "as of May 2025") rather than "last year" or "currently".
        - For trends in pay over time use `search_series` / `get_series` (e.g. average hourly
          earnings); OEWS is a yearly snapshot.

        ## Output format
        `reference_period`; `rows` (area_id, area_name, occupation_code, occupation,
        employment, jobs_per_1000, location_quotient, hourly/annual mean and median, annual
        p10/p90, annual_only, top_coded); `national`; `failed`; `notes`; `sources`.

        IMPORTANT: Never state a number for a field listed in `top_coded`; say it is at or above
        the OEWS reporting cap, because BLS does not publish the exact value.
        """
        with tool_errors():
            return services.get_occupation_wages(
                occupation_codes,
                upstream,
                area_ids=area_ids,
                rank_across=rank_across,
                sort_by=sort_by,
                limit=limit,
                year=year,
                response_format=response_format,
            )

    return mcp
