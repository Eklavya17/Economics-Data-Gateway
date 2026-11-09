"""MCP resources: read-only reference documents under ``econ://`` (Phase 5, DESIGN.md §3).

Tools are what the model *calls*. Resources are documents a client or user *attaches* as
context (Claude Code: ``@econ-gateway:econ://...``; Claude Desktop: the attachment menu).
They answer "what is available and how should it be read", while numbers still come from tools.

=============================  ==============================================================
``econ://catalog``             every curated series (ID, title, units, frequency, SA/NSA)
``econ://catalog/{category}``  one category, e.g. ``econ://catalog/unemployment``
``econ://series/{series_id}``  ``describe_series`` + latest value for one ID
``econ://releases``            last and next dates of the headline BLS/BEA releases
``econ://methodology``         index of methodology notes
``econ://methodology/{topic}`` one note, e.g. ``econ://methodology/seasonal-adjustment``
``econ://status``              gateway version, toolsets, upstream health and BLS quota
=============================  ==============================================================
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from pydantic import BaseModel

from econ_gateway.services import methodology, reference, releases
from econ_gateway.services.errors import GatewayError
from econ_gateway.services.upstream import Upstream

JSON = "application/json"
MARKDOWN = "text/markdown"

M = TypeVar("M", bound=BaseModel)


def _json(produce: Callable[[], M]) -> str:
    """Run a service and serialize its model; expected failures become ``ResourceError``.

    ``ResourceError`` messages reach the client even with ``mask_error_details=True``, so the
    reader learns what to do next (e.g. the list of valid categories).
    """
    try:
        # Compact, and without null fields: the reader is a model, and every token counts.
        return produce().model_dump_json(exclude_none=True)
    except GatewayError as exc:
        raise ResourceError(str(exc)) from exc


def build(upstream: Upstream, toolsets: Sequence[str]) -> FastMCP:
    """Create the resources sub-server bound to ``upstream``.

    Args:
        upstream: Data access.
        toolsets: Mounted toolsets, reported by ``econ://status``.
    """
    mcp: FastMCP = FastMCP("resources", mask_error_details=True)

    @mcp.resource("econ://catalog", name="catalog", mime_type=JSON)
    def catalog() -> str:
        """Every series in the gateway's curated, validated catalog (~300 series, ~15K tokens).

        Each entry has the series ID to pass to the data tools, its title, category, units,
        frequency, seasonal adjustment and geography (omitted for the United States). Prefer
        econ://catalog/{category} for one topic, or the search_series tool to find a series by
        description.
        """
        return _json(lambda: reference.catalog_listing(upstream))

    @mcp.resource("econ://catalog/{category}", name="catalog_category", mime_type=JSON)
    def catalog_category(category: str) -> str:
        """The catalog series in one category: unemployment, wages, inflation, employment, etc.

        The body lists every category with its count, so an unknown category is easy to fix.
        """
        return _json(lambda: reference.catalog_listing(upstream, category.strip().lower()))

    @mcp.resource("econ://series/{series_id}", name="series", mime_type=JSON)
    def series(series_id: str) -> str:
        """One series: metadata, coverage, release schedule and latest value.

        Combines describe_series and get_latest for a single BLS or FRED ID, e.g.
        econ://series/LNS14000000 (U.S. unemployment rate).
        """
        return _json(lambda: reference.series_resource(series_id, upstream))

    @mcp.resource("econ://releases", name="releases", mime_type=JSON)
    def release_calendar() -> str:
        """Last and next release dates of the headline BLS and BEA releases.

        Covers the jobs report, CPI, jobless claims, JOLTS, the ECI, state and metro
        unemployment, PPI, productivity, personal income (PCE) and GDP. No newer data exists
        before a release's next date.
        """
        return _json(lambda: releases.release_calendar(upstream))

    @mcp.resource("econ://methodology", name="methodology", mime_type=MARKDOWN)
    def methodology_index() -> str:
        """Index of methodology notes: seasonal adjustment, revisions, OEWS, QCEW, LAUS, etc."""
        return methodology.methodology_index()

    @mcp.resource("econ://methodology/{topic}", name="methodology_topic", mime_type=MARKDOWN)
    def methodology_topic(topic: str) -> str:
        """One methodology note, e.g. econ://methodology/rates-and-changes."""
        try:
            return methodology.methodology_topic(topic)
        except GatewayError as exc:
            raise ResourceError(str(exc)) from exc

    @mcp.resource("econ://status", name="status", mime_type=JSON)
    def status() -> str:
        """Gateway version, mounted toolsets, whether API keys are configured, upstream health.

        Health covers circuit-breaker state, rate-limit headroom and BLS daily quota use. Key
        values are never included.
        """
        return _json(lambda: reference.gateway_status(upstream, list(toolsets)))

    return mcp
