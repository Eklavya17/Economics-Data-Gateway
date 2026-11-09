"""Gateway entry point: builds the FastMCP server and runs it over stdio or HTTP.

Run locally:      econ-gateway                      (stdio, for Claude Desktop / Claude Code)
                  econ-gateway --transport http     (Streamable HTTP on 127.0.0.1:8000/mcp)
Inspect:          fastmcp dev inspector src/econ_gateway/server.py
"""

from __future__ import annotations

import argparse
import logging

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from econ_gateway import __version__, prompts, resources
from econ_gateway.config import Settings, get_settings
from econ_gateway.consts import SERVER_NAME
from econ_gateway.http_server import build_auth, register_health_routes, serve_http
from econ_gateway.infra.logs import configure_logging
from econ_gateway.infra.telemetry import configure_telemetry
from econ_gateway.services.upstream import LiveUpstream, Upstream
from econ_gateway.toolsets import TOOLSET_BUILDERS

log = logging.getLogger(__name__)

# Read by the client model at connection time: what the server is for and how to use it.
INSTRUCTIONS = """\
Live, official U.S. economic data from the Bureau of Labor Statistics (BLS) and the Federal \
Reserve Economic Data service (FRED): employment, unemployment, wages, prices/inflation, job \
openings, productivity, GDP, interest rates and jobless claims, nationally and for states, \
metros and counties; plus wages by occupation (OEWS) and jobs and pay by county and industry \
(QCEW).

Which tool:
- Time series (national, state, metro): search_series -> get_latest (current value) or \
get_series (history, year-over-year changes). describe_series explains one series.
- A place: find_area -> area_id plus local unemployment series IDs (use them with get_latest).
- Pay for a job: find_occupation (+ find_area) -> get_occupation_wages (specific places, or \
rank_across all states/metros).
- Jobs and pay by industry in a county/metro/state: find_area -> get_area_employment.
- Are wages keeping up with inflation: search_series (wage series) -> real_wage_growth.
- Rank or compare several series (industries, states, vs CPI): search_series -> compare_series.
  Prefer these over computing growth rates or rankings yourself.
- Is newer data coming? describe_series returns the series' release with its next date.

Resources (if your client can read them): econ://methodology explains seasonal adjustment, \
revisions, OEWS, QCEW and LAUS; econ://catalog lists every curated series; econ://releases \
has the release calendar.

Rules: use only numbers returned by these tools; never invent series IDs, area_ids or SOC \
codes; quote the period each number refers to; cite the url/sources; data is as of the latest \
agency release, which lags today's date. If a period has no published value (null), say it was \
not published and never present a neighbouring period's value under its label.
"""


def create_server(
    settings: Settings | None = None,
    upstream: Upstream | None = None,
    *,
    auth: AuthProvider | None = None,
) -> FastMCP:
    """Build the gateway with the configured toolsets mounted.

    Args:
        settings: Defaults to environment settings.
        upstream: Data access; defaults to the live BLS/FRED clients. Tests pass a fake.
        auth: Token verifier for the HTTP transport (``http_server.build_auth``); stdio
            ignores it.

    Returns:
        The ready-to-run server.
    """
    settings = settings or get_settings()
    upstream = upstream or LiveUpstream()
    mcp: FastMCP = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        mask_error_details=True,  # unexpected exceptions reach the client as a generic message
    )
    # Order matters: the first middleware added runs first on the way in.
    # transform_errors=False: by default this middleware turns ToolError into a JSON-RPC
    # protocol error ("Internal error"), which hides our actionable message from the model.
    # The MCP spec wants tool failures returned as results with isError=true (DESIGN.md #7).
    # We keep the middleware for its error logging and per-error counts.
    mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=False, transform_errors=False))
    mcp.add_middleware(TimingMiddleware())
    for name in settings.toolsets:
        mcp.mount(TOOLSET_BUILDERS[name](upstream))
    # Resources and prompts (Phase 5): reference documents and question templates.
    mcp.mount(resources.build(upstream, list(settings.toolsets)))
    mcp.mount(prompts.build(list(settings.toolsets)))
    # HTTP only (Phase 7): liveness and readiness probes for orchestrators.
    register_health_routes(mcp, upstream, settings)
    return mcp


def _parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="econ-gateway", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=settings.transport,
        help="stdio for local clients (default); http for Streamable HTTP at /mcp",
    )
    parser.add_argument("--host", default=settings.host, help="HTTP bind address")
    parser.add_argument("--port", type=int, default=settings.port, help="HTTP port")
    return parser.parse_args()


def main() -> None:
    """CLI entry point (``econ-gateway``): configure logging, then serve."""
    settings = get_settings()
    args = _parse_args(settings)
    configure_logging(settings.log_level)
    configure_telemetry(settings.telemetry, service_name=SERVER_NAME, version=__version__)
    log.info(
        "starting",
        extra={
            "version": __version__,
            "transport": args.transport,
            "toolsets": settings.toolsets,
            "bls_key": settings.has_bls_key,
            "fred_key": settings.has_fred_key,
            "auth": settings.auth_mode,
            "telemetry": settings.telemetry,
        },
    )
    if args.transport == "http":
        server = create_server(settings, auth=build_auth(settings))
        serve_http(server, settings, host=args.host, port=args.port)
    else:
        mcp.run()


# Module-level instance for `fastmcp run` / `fastmcp dev inspector` / fastmcp.json.
mcp = create_server()

if __name__ == "__main__":
    main()
