"""End-to-end MCP tests: real FastMCP server and client, in memory, with a fake upstream.

Exact tool names and schemas are pinned by tests/snapshots/tool_schemas.json (see
test_tool_docs.py). These tests check the *quality rules* every tool must follow.
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from conftest import FakeUpstream
from econ_gateway.config import Settings
from econ_gateway.server import create_server

MAX_TOOLS = 12  # DESIGN.md §3: more tools make the client model choose worse
NO_USAGE_SECTION = {"describe_series"}  # single-purpose; its summary line is enough


@pytest.fixture
async def client():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as c:
        yield c


async def test_every_tool_follows_the_quality_rules(client):
    tools = await client.list_tools()
    assert 0 < len(tools) <= MAX_TOOLS
    for tool in tools:
        assert tool.output_schema is not None, f"{tool.name} must declare structured output"
        assert tool.annotations is not None and tool.annotations.read_only_hint, tool.name
        description = tool.description or ""
        assert "## Usage" in description or tool.name in NO_USAGE_SECTION, tool.name
        assert "## Output format" in description or tool.name in NO_USAGE_SECTION, tool.name
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description") or prop.get("type") == "integer", (
                f"{tool.name}.{name}: every non-trivial parameter needs a description"
            )


async def test_get_latest_returns_structured_content(client):
    result = await client.call_tool("get_latest", {"series_ids": ["LNS14000000"]})
    data = result.structured_content
    assert data is not None
    assert data["results"][0]["latest_period"] == "2026-09"
    assert data["results"][0]["meta"]["url"].endswith("LNS14000000")


async def test_compute_tools_end_to_end(client):
    real = await client.call_tool("real_wage_growth", {"wage_series_id": "CES3000000003"})
    assert real.structured_content is not None
    assert real.structured_content["real_wage_rising"] is True
    ranked = await client.call_tool(
        "compare_series",
        {"series_ids": ["CES0500000003", "CES3000000003"], "measure": "pct_change_year_ago"},
    )
    assert ranked.structured_content is not None
    assert ranked.structured_content["rows"][0]["rank"] == 1


async def test_service_errors_reach_the_model_as_tool_errors(client):
    with pytest.raises(ToolError, match="search_series"):
        await client.call_tool("describe_series", {"series_id": "LNU99999999"})


async def test_invalid_series_id_is_rejected_by_schema(client):
    with pytest.raises(ToolError):
        await client.call_tool("get_latest", {"series_ids": ["not a series; drop table"]})


async def test_toolsets_setting_controls_what_is_mounted():
    server = create_server(Settings(toolsets=["wages"]), upstream=FakeUpstream())
    async with Client(server) as c:
        names = {t.name for t in await c.list_tools()}
    assert "get_occupation_wages" in names and "find_area" in names  # core added for find_area
    assert "real_wage_growth" not in names and "get_area_employment" not in names


async def test_empty_toolsets_mount_nothing():
    server = create_server(Settings(toolsets=[]), upstream=FakeUpstream())
    async with Client(server) as c:
        assert await c.list_tools() == []
