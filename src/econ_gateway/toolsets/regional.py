"""Regional toolset: jobs, establishments and pay by county, metro and state (BLS QCEW).

Flow for the client model: ``find_area`` (core toolset) → ``get_area_employment``.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from econ_gateway import services
from econ_gateway.consts import (
    AREA_ID_PATTERN,
    EMPLOYMENT_MAX_AREAS,
    EMPLOYMENT_MAX_INDUSTRIES,
    TOOL_TIMEOUT_S,
)
from econ_gateway.models import AreaEmploymentResponse
from econ_gateway.services.employment import IndustryKey
from econ_gateway.services.upstream import Upstream
from econ_gateway.toolsets._common import READ_ONLY_EXTERNAL, tool_errors

AreaId = Annotated[str, Field(pattern=AREA_ID_PATTERN)]


def build(upstream: Upstream) -> FastMCP:
    """Create the regional toolset as a FastMCP sub-server bound to ``upstream``."""
    mcp: FastMCP = FastMCP("regional", mask_error_details=True)

    @mcp.tool(annotations=READ_ONLY_EXTERNAL, timeout=TOOL_TIMEOUT_S)
    def get_area_employment(
        area_ids: Annotated[
            list[AreaId],
            Field(
                min_length=1,
                max_length=EMPLOYMENT_MAX_AREAS,
                description="1-10 places from find_area, e.g. ['county:48453', 'metro:12420'].",
            ),
        ],
        industries: Annotated[
            list[IndustryKey],
            Field(
                min_length=1,
                max_length=EMPLOYMENT_MAX_INDUSTRIES,
                description=(
                    "Industry slices. 'total' = all jobs; 'private'; government by level; or "
                    "sectors such as 'construction', 'manufacturing', 'health_care_social_"
                    "assistance', 'leisure_hospitality'. List several for a breakdown."
                ),
            ),
        ] = ["total"],  # noqa: B006  # pydantic copies defaults; this is the schema default
        year: Annotated[
            int | None,
            Field(ge=2015, le=2100, description="Calendar year; give with quarter."),
        ] = None,
        quarter: Annotated[
            int | None,
            Field(ge=1, le=4, description="1-4; give with year. Omit both for the latest."),
        ] = None,
    ) -> AreaEmploymentResponse:
        """Get jobs, establishments and average weekly wages for counties, metros or states.

        ## Usage
        For local job counts and pay by industry ("how many people work in health care in
        Travis County?", "which industries employ the most in Austin?"). Data comes from
        QCEW, a near-census of employers; it is the only source here with county detail.
        For monthly local unemployment use `find_area` → `laus` series → `get_latest`.

        ## Tips
        - QCEW lags about 5-6 months; `reference_period` says which quarter you got.
        - It is not seasonally adjusted: use the *_year_ago fields for change, not the
          previous quarter.
        - `disclosed: false` means BLS suppressed the cell; never treat it as zero.

        ## Output format
        `reference_period` (e.g. 2026-Q1); `rows` (area, industry, ownership, disclosed,
        establishments, employment, avg_weekly_wage, employment_change_year_ago,
        employment_pct_change_year_ago, avg_weekly_wage_pct_change_year_ago); `failed`;
        `notes`; `sources`.
        """
        with tool_errors():
            return services.get_area_employment(
                area_ids,
                upstream,
                industries=list(industries),
                year=year,
                quarter=quarter,
            )

    return mcp
