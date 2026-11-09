"""MCP prompts: reusable question templates a user picks in their client (Phase 5).

In Claude Code they appear as slash commands (``/mcp__econ-gateway__occupation_wages``); in
Claude Desktop under the attachment menu. A prompt only fills in a user message that names the
tools to call and the reporting rules. The gateway itself writes no analysis (DESIGN.md
decisions 12 and 31); the client's model does the answering.

A prompt is registered only when every toolset it relies on is mounted, so it never points
the model at a tool the client cannot see.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.prompts import Message
from pydantic import Field

REPORTING_RULES = """\
Rules: use only numbers returned by the econ-gateway tools, and never invent series IDs, \
area_ids or SOC codes. Quote the period each number refers to. Say "preliminary" for \
preliminary values, and if meta.stale_as_of is set, say the data may not include the latest \
release. Describe changes in rates in percentage points. Cite the url fields."""

# Prompt name -> toolsets whose tools it tells the model to call.
REQUIRED_TOOLSETS: dict[str, set[str]] = {
    "occupation_wages": {"core", "wages"},
    "labor_market_snapshot": {"core"},
    "local_economy": {"core", "regional"},
    "real_wage_check": {"core", "compute"},
}


def _message(*paragraphs: str) -> list[Message]:
    return [Message("\n\n".join([*paragraphs, REPORTING_RULES]))]


def build(toolsets: Sequence[str]) -> FastMCP:
    """Create the prompts sub-server with the prompts that ``toolsets`` can serve.

    Args:
        toolsets: Mounted toolsets.
    """
    mcp: FastMCP = FastMCP("prompts", mask_error_details=True)
    mounted = set(toolsets)

    def enabled(name: str) -> bool:
        return REQUIRED_TOOLSETS[name] <= mounted

    if enabled("occupation_wages"):

        @mcp.prompt(name="occupation_wages")
        def occupation_wages(
            occupation: Annotated[
                str, Field(description="Occupation in plain words, e.g. 'registered nurses'.")
            ],
            place: Annotated[
                str,
                Field(description="State or metro area, e.g. 'Texas' or 'Austin, TX'."),
            ] = "the United States",
        ) -> list[Message]:
            """What an occupation earns in a state or metro area (OEWS), with the U.S. median."""
            return _message(
                f"What do {occupation} earn in {place}?",
                "Steps:\n"
                f"1. find_occupation with '{occupation}'; pick the SOC code whose title matches "
                "and say which one you used if several are plausible.\n"
                f"2. find_area with '{place}' to get its area_id (skip this for the United "
                "States: national rows are always included).\n"
                "3. get_occupation_wages with those codes and area_ids.",
                "Report the median and mean annual wage (and hourly when published), "
                "employment, the 10th to 90th percentile range and the U.S. median for "
                "comparison. State the reference period (May of the year shown) and that OEWS "
                "is a yearly snapshot, not a trend. Report top-coded values as 'at least'.",
            )

    if enabled("labor_market_snapshot"):

        @mcp.prompt(name="labor_market_snapshot")
        def labor_market_snapshot(
            place: Annotated[
                str,
                Field(description="'the United States', a state or a metro area."),
            ] = "the United States",
        ) -> list[Message]:
            """Current state of the labor market: unemployment, jobs, pay, inflation, claims."""
            return _message(
                f"Give me a snapshot of the labor market in {place}.",
                "Steps:\n"
                "- For the United States: get_latest with LNS14000000 (unemployment rate), "
                "CES0000000001 (payroll jobs), CES0500000003 (average hourly earnings), "
                "CUSR0000SA0 (CPI) and ICSA (initial jobless claims). Use search_series for "
                "anything else you need, such as job openings.\n"
                "- For a state or metro: find_area to get its area_id and unemployment series "
                "IDs, then get_latest on those IDs. Add the U.S. unemployment rate for "
                "comparison.",
                "Report each latest value with its period, the change from the previous "
                "period and from a year earlier. Keep it to a short paragraph plus a table.",
            )

    if enabled("local_economy"):

        @mcp.prompt(name="local_economy")
        def local_economy(
            place: Annotated[
                str,
                Field(description="County, metro area or state, e.g. 'Travis County, TX'."),
            ],
        ) -> list[Message]:
            """Jobs, pay by industry and unemployment for a county, metro area or state (QCEW)."""
            return _message(
                f"How is the local economy doing in {place}?",
                "Steps:\n"
                f"1. find_area with '{place}'; use the best match and note its level.\n"
                "2. get_area_employment for that area_id with industries total, "
                "goods_producing, construction, manufacturing, health_care_social_assistance, "
                "professional_scientific_technical and leisure_hospitality.\n"
                "3. get_latest on the area's unemployment-rate series ID from find_area.",
                "Report total jobs and the average weekly wage with their year-over-year "
                "changes, the fastest- and slowest-growing industries, and the unemployment "
                "rate. Say which quarter the QCEW data covers (it lags about two quarters), "
                "and that suppressed industries (disclosed: false) are withheld, not zero.",
            )

    if enabled("real_wage_check"):

        @mcp.prompt(name="real_wage_check")
        def real_wage_check(
            wage_measure: Annotated[
                str,
                Field(
                    description=(
                        "Which pay to check, e.g. 'average hourly earnings, private' or "
                        "'manufacturing average hourly earnings'."
                    )
                ),
            ] = "average hourly earnings of all private employees",
            deflator: Annotated[
                Literal["cpi_u", "cpi_w", "pce"],
                Field(description="Price index: cpi_u (usual), cpi_w or pce."),
            ] = "cpi_u",
            since: Annotated[
                str,
                Field(description="Optional start of the window, e.g. '2019' or '2020-02'."),
            ] = "",
        ) -> list[Message]:
            """Are wages keeping up with inflation? Real wage growth for one pay measure."""
            window = f" since {since}" if since else ""
            start_arg = f" and start_date='{since}'" if since else ""
            return _message(
                f"Are {wage_measure} keeping up with inflation{window}?",
                "Steps:\n"
                f"1. search_series for '{wage_measure}' and pick a wage LEVEL series (dollars "
                "or an index, not a percent change).\n"
                f"2. real_wage_growth with that wage_series_id, deflator='{deflator}'"
                f"{start_arg}.",
                "Report nominal wage growth, inflation and real growth over the latest year, "
                "whether real wages are rising, and the change over the whole window. Say "
                "which price index was used and note any period where wages are published "
                "ahead of prices.",
            )

    return mcp
