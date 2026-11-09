from typing import get_args

import pandas as pd
import pytest

from conftest import TODAY
from econ_gateway import services
from econ_gateway.services.areas import build_area_table, resolve_area_ids
from econ_gateway.services.employment import INDUSTRIES, IndustryKey
from econ_gateway.services.errors import InvalidRequestError
from fake_places import OEWS_AREAS, QCEW_AREAS

# ---------------------------------------------------------------- area table


def test_area_table_joins_codes_across_programs():
    table = build_area_table(OEWS_AREAS, QCEW_AREAS).set_index("area_id")
    assert table.loc["state:48", "qcew_code"] == "48000"
    assert table.loc["metro:12420", "qcew_code"] == "C1242"
    assert pd.isna(table.loc["county:48453", "oews_code"])  # counties have no OEWS code


def test_area_table_skips_nonmetro_and_unknown_buckets():
    ids = set(build_area_table(OEWS_AREAS, QCEW_AREAS)["area_id"])
    assert "metro:4800001" not in ids and "county:48999" not in ids
    assert {"us", "state:48", "metro:12420", "county:48453", "county:48015"} <= ids


@pytest.mark.parametrize(
    ("area_id", "expected"),
    [
        # Formats verified live against the BLS API on 2026-11-07.
        ("state:48", "LASST480000000000003"),
        ("metro:12420", "LAUMT481242000000003"),
        ("county:48453", "LAUCN484530000000003"),
        ("us", "LNS14000000"),
    ],
)
def test_laus_unemployment_rate_ids(upstream, area_id, expected):
    (record,), _ = resolve_area_ids([area_id], upstream)
    laus = record.laus()
    assert laus is not None and laus.unemployment_rate == expected


def test_metro_and_county_laus_are_flagged_not_seasonally_adjusted(upstream):
    records, _ = resolve_area_ids(["state:48", "metro:12420"], upstream)
    assert [r.laus().seasonal_adjustment for r in records] == ["SA", "NSA"]  # type: ignore[union-attr]


def test_resolve_unknown_area_points_to_find_area(upstream):
    records, failed = resolve_area_ids(["state:99"], upstream)
    assert records == [] and "find_area" in failed[0].hint


# ---------------------------------------------------------------- find_area / find_occupation


def test_find_area_state_abbreviation(upstream):
    out = services.find_area("TX", upstream, limit=3)
    assert out.results[0].area_id == "state:48"
    assert out.results[0].laus is not None


def test_find_area_bare_city_prefers_metro_and_lists_each_place_once(upstream):
    # Live regression: "Austin" returned Austin County twice, above the Austin metro.
    out = services.find_area("Austin", upstream, limit=5)
    ids = [r.area_id for r in out.results]
    assert ids[0] == "metro:12420"
    assert len(ids) == len(set(ids))


def test_find_area_explicit_county_still_wins(upstream):
    out = services.find_area("Austin County", upstream, limit=3)
    assert out.results[0].area_id == "county:48015"


def test_find_area_level_filter_separates_county_from_metro(upstream):
    out = services.find_area("Austin", upstream, level="county", limit=3)
    assert {r.level for r in out.results} == {"county"}
    assert out.results[0].has_occupation_wages is False


def test_find_occupation_uses_aliases(upstream):
    out = services.find_occupation("nurse", upstream, limit=3)
    assert out.results[0].occupation_code == "29-1141"
    assert out.results[0].group == "detailed"


# ---------------------------------------------------------------- get_occupation_wages


def test_wages_for_specific_places_with_national_comparison(upstream):
    out = services.get_occupation_wages(
        ["29-1141"], upstream, area_ids=["state:48", "metro:12420"], limit=10, today=TODAY
    )
    assert out.reference_period == "May 2025"
    assert [r.area_id for r in out.rows] == ["state:48", "metro:12420"]
    assert out.rows[0].annual_median == 89_000
    assert out.national[0].area_id == "us" and out.national[0].annual_median == 97_550
    assert out.sources == ["https://www.bls.gov/oes/current/oes291141.htm"]
    assert out.rows[0].annual_p25 is None  # concise format omits p25/p75


def test_wages_discover_latest_year_by_trying(upstream):
    services.get_occupation_wages(["29-1141"], upstream, limit=5, today=TODAY)
    # 2025 is last year relative to TODAY and exists, so nothing older is tried.
    assert ("oews", (2025, "national")) in upstream.calls
    assert not any(c == ("oews", (2024, "national")) for c in upstream.calls)


def test_wages_counties_and_missing_estimates_fail_with_next_steps(upstream):
    out = services.get_occupation_wages(
        ["25-2021"], upstream, area_ids=["county:48453", "metro:12420"], limit=5, today=TODAY
    )
    reasons = {f.id: f for f in out.failed}
    assert "metro" in reasons["county:48453"].hint
    assert "suppressed" in reasons["25-2021@metro:12420"].hint
    assert out.national[0].annual_only and out.national[0].hourly_median is None


def test_ranking_puts_top_coded_first_and_never_reports_its_value(upstream):
    out = services.get_occupation_wages(
        ["15-1252"], upstream, rank_across="metros", limit=5, today=TODAY
    )
    first = out.rows[0]
    assert first.area_id == "metro:16980"
    assert "annual_median" in first.top_coded and first.annual_median is None
    assert any("top_coded" in n for n in out.notes)


def test_ranking_orders_by_requested_measure(upstream):
    out = services.get_occupation_wages(
        ["29-1141"], upstream, rank_across="states", sort_by="annual_median", limit=5, today=TODAY
    )
    assert [r.area_id for r in out.rows] == ["state:17", "state:48"]


def test_wages_reject_areas_and_ranking_together(upstream):
    with pytest.raises(InvalidRequestError, match="not both"):
        services.get_occupation_wages(
            ["29-1141"], upstream, area_ids=["state:48"], rank_across="states", limit=5
        )


def test_wages_unknown_soc_code_fails_with_hint(upstream):
    out = services.get_occupation_wages(["99-9999"], upstream, limit=5, today=TODAY)
    assert out.failed[0].id == "99-9999" and "find_occupation" in out.failed[0].hint


def test_detailed_format_adds_quartiles(upstream):
    out = services.get_occupation_wages(
        ["29-1141"], upstream, area_ids=["state:48"], limit=5, response_format="detailed",
        today=TODAY,
    )  # fmt: skip
    assert out.rows[0].annual_p25 == pytest.approx(71_200)


# ---------------------------------------------------------------- get_area_employment


def test_industry_enum_matches_table():
    assert set(get_args(IndustryKey)) == set(INDUSTRIES)


def test_employment_discovers_latest_quarter(upstream):
    out = services.get_area_employment(["county:48453"], upstream, today=TODAY)
    assert out.reference_period == "2026-Q1"
    (row,) = out.rows
    assert row.employment == 931_218 and row.ownership == "total"
    assert out.sources == ["https://data.bls.gov/cew/data/api/2026/1/area/48453.csv"]


def test_suppressed_cells_are_not_zero(upstream):
    out = services.get_area_employment(
        ["metro:12420"], upstream, industries=["construction", "health_care_social_assistance"],
        today=TODAY,
    )  # fmt: skip
    construction, health = out.rows
    assert construction.disclosed is False and construction.employment is None
    assert health.employment == 100_793 and health.ownership == "private"
    assert any("not zero" in n for n in out.notes)
    assert any("private-sector" in n for n in out.notes)


def test_missing_industry_row_is_reported(upstream):
    out = services.get_area_employment(
        ["state:48"], upstream, industries=["information"], today=TODAY
    )
    assert out.rows == [] and out.failed[0].id == "information@state:48"


def test_year_without_quarter_is_rejected(upstream):
    with pytest.raises(InvalidRequestError, match="both year and quarter"):
        services.get_area_employment(["state:48"], upstream, year=2025, today=TODAY)
