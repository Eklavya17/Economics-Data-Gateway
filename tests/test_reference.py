"""Phase 5: release schedules, resources and prompts (services and the MCP surface)."""

from __future__ import annotations

import json
import re
from typing import get_args

import pytest
from fastmcp import Client

from conftest import TODAY, FakeUpstream
from econ_gateway import prompts
from econ_gateway.config import Settings
from econ_gateway.server import create_server
from econ_gateway.services import methodology, reference, releases
from econ_gateway.services.describe import describe_series
from econ_gateway.services.employment import IndustryKey
from econ_gateway.services.errors import InvalidRequestError
from econ_gateway.sources.catalog import load_catalog

TOOL_NAME = re.compile(
    r"\b(search_series|describe_series|get_latest|get_series|find_area|find_occupation|"
    r"get_occupation_wages|get_area_employment|real_wage_growth|compare_series|"
    r"get_\w+|find_\w+)\b"
)

# ---------------------------------------------------------------- release schedules


def test_series_release_has_last_and_next_dates(upstream: FakeUpstream):
    info = releases.series_release("CES0500000003", "BLS", upstream, TODAY)
    assert info is not None
    assert (info.name, info.last_release_date, info.next_release_date) == (
        "Employment Situation",
        "2026-11-06",
        "2026-12-04",
    )
    assert info.url == "https://fred.stlouisfed.org/release?rid=50"


def test_series_without_known_release_has_none(upstream: FakeUpstream):
    assert releases.series_release("PCEPI", "FRED", upstream, TODAY) is None


def test_describe_series_includes_release(upstream: FakeUpstream):
    out = describe_series("ICSA", upstream, today=TODAY)
    assert out.release is not None
    assert out.release.next_release_date == "2026-11-12"


def test_release_calendar_sorted_by_next_release(upstream: FakeUpstream):
    calendar = releases.release_calendar(upstream, today=TODAY)
    upcoming = [r.name for r in calendar.releases if r.next_release_date]
    assert upcoming == [
        "Consumer Price Index",
        "Unemployment Insurance Weekly Claims Report",
        "Employment Situation",
    ]
    assert all(r.release_id != 18 for r in calendar.releases)  # daily rates are left out


def test_release_calendar_explains_missing_fred_key():
    calendar = releases.release_calendar(FakeUpstream(fred_key=False), today=TODAY)
    assert any("FRED_API_KEY is not set" in note for note in calendar.notes)


# ---------------------------------------------------------------- catalog, series, status


def test_catalog_listing_all_and_by_category(upstream: FakeUpstream):
    everything = reference.catalog_listing(upstream)
    assert everything.count == 9
    assert everything.categories == {"claims": 1, "inflation": 3, "unemployment": 2, "wages": 3}
    wages = reference.catalog_listing(upstream, "wages")
    assert [e.series_id for e in wages.entries] == [
        "CES0500000003",
        "CES3000000003",
        "CIU2020000000000I",
    ]
    assert wages.entries[0].frequency == "monthly"


def test_unknown_category_lists_valid_ones(upstream: FakeUpstream):
    with pytest.raises(InvalidRequestError, match="claims, inflation, unemployment, wages"):
        reference.catalog_listing(upstream, "gdp")


def test_series_resource_combines_description_and_latest(upstream: FakeUpstream):
    body = reference.series_resource("ces0500000003", upstream, today=TODAY)
    assert body.description.meta.series_id == "CES0500000003"
    assert body.latest is not None and body.latest.latest_period == "2026-09"
    assert body.description.release is not None


def test_series_resource_rejects_malformed_id(upstream: FakeUpstream):
    with pytest.raises(InvalidRequestError, match="not a valid series ID"):
        reference.series_resource("../etc/passwd", upstream)


def test_status_reports_keys_without_values(upstream: FakeUpstream, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "super-secret-value")
    status = reference.gateway_status(upstream, ["core"])
    assert status.fred_key_configured and status.bls_key_configured
    assert status.upstreams["BLS"] == {"circuit": {"state": "closed"}}
    assert "super-secret-value" not in status.model_dump_json()


# ---------------------------------------------------------------- methodology


def test_every_methodology_note_has_title_and_summary():
    topics = methodology.topics()
    assert {"seasonal-adjustment", "revisions", "oews-wages", "area-ids"} <= set(topics)
    for topic in topics.values():
        assert topic.title and not topic.title.startswith("#")
        assert len(topic.summary) > 40, topic.slug
    index = methodology.methodology_index()
    assert all(f"econ://methodology/{slug}" in index for slug in topics)


def test_unknown_methodology_topic_lists_valid_ones():
    with pytest.raises(InvalidRequestError, match="seasonal-adjustment"):
        methodology.methodology_topic("astrology")


# ---------------------------------------------------------------- MCP surface


async def test_resources_and_prompts_are_listed():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        templates = {t.uri_template for t in await client.list_resource_templates()}
        prompt_names = {p.name for p in await client.list_prompts()}
    assert uris == {"econ://catalog", "econ://releases", "econ://methodology", "econ://status"}
    assert templates == {
        "econ://catalog/{category}",
        "econ://series/{series_id}",
        "econ://methodology/{topic}",
    }
    assert prompt_names == set(prompts.REQUIRED_TOOLSETS)


async def test_read_series_resource_over_mcp():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        contents = await client.read_resource("econ://series/LNS14000000")
    body = json.loads(contents[0].text)  # type: ignore[union-attr]
    assert body["description"]["meta"]["units"] == "percent"
    assert body["latest"]["change_units"] == "percentage points"


async def test_resource_errors_reach_the_client():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        with pytest.raises(Exception, match="Valid categories: claims"):
            await client.read_resource("econ://catalog/gdp")


async def test_methodology_resource_is_markdown():
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        templates = await client.list_resource_templates()
        contents = await client.read_resource("econ://methodology/rates-and-changes")
    mime = {t.uri_template: t.mime_type for t in templates}
    assert mime["econ://methodology/{topic}"] == "text/markdown"
    assert "percentage points" in contents[0].text  # type: ignore[union-attr]


async def test_prompts_follow_mounted_toolsets():
    server = create_server(Settings(toolsets=["core"]), upstream=FakeUpstream())
    async with Client(server) as client:
        names = {p.name for p in await client.list_prompts()}
    assert names == {"labor_market_snapshot"}


PROMPT_ARGS = {
    "occupation_wages": {"occupation": "registered nurses", "place": "Texas"},
    "labor_market_snapshot": {},
    "local_economy": {"place": "Travis County, TX"},
    "real_wage_check": {"since": "2019"},
}


@pytest.mark.parametrize("name", sorted(PROMPT_ARGS))
async def test_prompts_only_name_tools_that_exist(name: str):
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        tools = {t.name for t in await client.list_tools()}
        result = await client.get_prompt(name, PROMPT_ARGS[name])
    text = result.messages[0].content.text  # type: ignore[union-attr]
    named = set(TOOL_NAME.findall(text))
    assert named, "a prompt should tell the model which tools to call"
    assert named <= tools
    assert "never invent series IDs" in text


def test_methodology_notes_only_name_tools_that_exist():
    known = {
        "search_series", "describe_series", "get_latest", "get_series", "find_area",
        "find_occupation", "get_occupation_wages", "get_area_employment", "real_wage_growth",
        "compare_series",
    }  # fmt: skip
    for topic in methodology.topics().values():
        assert set(TOOL_NAME.findall(topic.text)) <= known, topic.slug


async def test_prompt_ids_and_industries_are_real():
    """Hard-coded series IDs exist in the real catalog; industry keys exist in the QCEW list."""
    async with Client(create_server(Settings(), upstream=FakeUpstream())) as client:
        snapshot = await client.get_prompt("labor_market_snapshot", {})
        local = await client.get_prompt("local_economy", {"place": "Austin"})
    snapshot_text = snapshot.messages[0].content.text  # type: ignore[union-attr]
    local_text = local.messages[0].content.text  # type: ignore[union-attr]
    catalog_ids = set(load_catalog()["series_id"])
    for series_id in ("LNS14000000", "CES0000000001", "CES0500000003", "CUSR0000SA0", "ICSA"):
        assert series_id in snapshot_text and series_id in catalog_ids
    industries = re.search(r"industries (.+?)\.\n", local_text)
    assert industries is not None
    keys = re.findall(r"[a-z_]+", industries.group(1).replace(" and ", ", "))
    assert set(keys) - {"and"} <= set(get_args(IndustryKey))
