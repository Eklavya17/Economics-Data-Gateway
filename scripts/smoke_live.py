"""Live smoke test over a real stdio connection, the way Claude Desktop talks to the gateway.

Starts ``econ-gateway`` as a subprocess, runs realistic tool calls against live BLS/FRED data,
reads the ``econ://`` resources and renders a prompt, and prints compact results plus the size
of each response. A corrupted stdout (e.g. a
stray print) would make the connection fail here.

    .venv/Scripts/python.exe scripts/smoke_live.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]

CALLS: list[tuple[str, dict[str, object]]] = [
    ("search_series", {"query": "unemployment rate", "geography": "Texas", "limit": 3}),
    ("search_series", {"query": "average hourly earnings", "geography": "Texas", "limit": 3}),
    ("get_latest", {"series_ids": ["LNS14000000", "CUSR0000SA0", "ICSA"]}),
    (
        "get_series",
        {"series_ids": ["CUSR0000SA0"], "transform": "pct_change_year_ago", "max_points": 6},
    ),
    ("describe_series", {"series_id": "UNRATE"}),
    ("get_latest", {"series_ids": ["NOT_A_SERIES"]}),
    # Phase 2: the PDF demo ("registered nurses in Texas") and local data.
    ("find_occupation", {"query": "registered nurse", "limit": 3}),
    ("find_area", {"query": "Texas", "level": "state", "limit": 2}),
    ("get_occupation_wages", {"occupation_codes": ["29-1141"], "area_ids": ["state:48"]}),
    (
        "get_occupation_wages",
        {"occupation_codes": ["29-1141"], "rank_across": "metros", "limit": 3},
    ),
    ("find_area", {"query": "Austin", "limit": 3}),
    ("get_latest", {"series_ids": ["LAUMT481242000000003", "LAUCN484530000000003"]}),
    (
        "get_area_employment",
        {
            "area_ids": ["county:48453"],
            "industries": [
                "total",
                "goods_producing",
                "construction",
                "health_care_social_assistance",
            ],
        },
    ),
    # Phase 3: compute tools.
    ("real_wage_growth", {"wage_series_id": "CES3000000003", "max_points": 3}),
    (
        "compare_series",
        {
            "series_ids": ["CES2000000003", "CES3000000003", "CES5000000003", "CES6500000003"],
            "measure": "pct_change_year_ago",
            "benchmark_series_id": "CUSR0000SA0",
        },
    ),
]

# Phase 5: resources (reference documents) and a prompt.
RESOURCES = [
    "econ://releases",
    "econ://status",
    "econ://series/UNRATE",
    "econ://catalog/claims",
    "econ://methodology/revisions",
]
PROMPTS: list[tuple[str, dict[str, str]]] = [
    ("occupation_wages", {"occupation": "registered nurses", "place": "Texas"}),
]


async def main() -> int:
    """Run ``CALLS``, ``RESOURCES`` and ``PROMPTS``; return non-zero if any errored."""
    transport = StdioTransport(
        command=sys.executable, args=["-m", "econ_gateway.server"], cwd=str(ROOT)
    )
    failures = 0
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"connected over stdio; tools: {[t.name for t in tools]}\n")
        for name, args in CALLS:
            result = await client.call_tool(name, args, raise_on_error=False)
            payload = result.structured_content or {}
            size = len(json.dumps(payload))
            status = "ERROR" if result.is_error else "ok"
            failures += result.is_error
            print(f"== {name}({json.dumps(args)}) -> {status}, {size} chars")
            print(json.dumps(payload, indent=1)[:900], "\n")
        for uri in RESOURCES:
            try:
                text = getattr((await client.read_resource(uri))[0], "text", "")
                print(f"== read {uri} -> ok, {len(text)} chars\n{text[:700]}\n")
            except Exception as exc:  # report and keep going, like the tool calls above
                failures += 1
                print(f"== read {uri} -> ERROR {exc}\n")
        for name, args in PROMPTS:
            result = await client.get_prompt(name, args)
            text = getattr(result.messages[0].content, "text", "")
            print(f"== prompt {name}({json.dumps(args)}) -> {len(text)} chars\n{text[:400]}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
