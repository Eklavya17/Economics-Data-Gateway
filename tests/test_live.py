"""Live checks against real BLS/FRED. Run with: LIVE_TESTS=1 pytest -m live"""

import os

import pytest

from econ_gateway import services
from econ_gateway.services.upstream import LiveUpstream

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("LIVE_TESTS"), reason="set LIVE_TESTS=1 to hit real APIs"),
]


@pytest.fixture(scope="module")
def upstream():
    return LiveUpstream()


def test_search_finds_texas_unemployment(upstream):
    out = services.search_series("unemployment rate", upstream, geography="Texas", limit=5)
    assert any("Texas" in (r.geography or "") or "Texas" in r.title for r in out.results)


def test_latest_national_unemployment_rate(upstream):
    out = services.get_latest(["LNS14000000"], upstream)
    (latest,) = out.results
    assert 2.0 < latest.latest_value < 15.0
    assert latest.change_units == "percentage points"


def test_pdf_demo_registered_nurse_wages_in_texas(upstream):
    (occ,) = services.find_occupation("registered nurse", upstream, limit=1).results
    (area,) = services.find_area("Texas", upstream, level="state", limit=1).results
    out = services.get_occupation_wages(
        [occ.occupation_code], upstream, area_ids=[area.area_id], limit=5
    )
    (row,) = out.rows
    assert row.occupation_code == "29-1141" and row.area_id == "state:48"
    assert row.annual_median is not None and 50_000 < row.annual_median < 200_000
    assert out.national and out.reference_period.startswith("May ")


def test_metro_laus_ids_resolve_and_rates_use_points(upstream):
    (metro,) = services.find_area("Austin", upstream, level="metro", limit=1).results
    assert metro.laus is not None
    (latest,) = services.get_latest([metro.laus.unemployment_rate], upstream).results
    assert latest.change_units == "percentage points"
    assert latest.pct_change_from_previous is None


def test_qcew_county_total_and_sector(upstream):
    out = services.get_area_employment(
        ["county:48453"], upstream, industries=["total", "health_care_social_assistance"]
    )
    assert [r.industry for r in out.rows] == ["total", "health_care_social_assistance"]
    assert all(r.employment and r.employment > 0 for r in out.rows if r.disclosed)


def test_real_wages_manufacturing(upstream):
    out = services.real_wage_growth("CES3000000003", upstream, max_points=3)
    assert out.latest is not None and out.deflator.series_id == "CUSR0000SA0"
    latest = out.latest
    assert latest.nominal_pct_change_year_ago is not None
    assert latest.inflation_pct_change_year_ago is not None
    assert latest.real_pct_change_year_ago is not None
    expected = (
        (1 + latest.nominal_pct_change_year_ago / 100)
        / (1 + latest.inflation_pct_change_year_ago / 100)
        - 1
    ) * 100
    assert latest.real_pct_change_year_ago == pytest.approx(expected, abs=2e-3)


def test_compare_industry_wage_growth_vs_cpi(upstream):
    industries = ["CES2000000003", "CES3000000003", "CES6500000003", "CES7000000003"]
    out = services.compare_series(
        industries, upstream, measure="pct_change_year_ago", benchmark_series_id="CUSR0000SA0"
    )
    assert out.benchmark is not None and not out.failed
    # Every row is evaluated at the benchmark's period, so the gaps compare the same months.
    assert {r.latest_period for r in out.rows} == {out.benchmark.latest_period}


def test_series_mixed_sources(upstream):
    if not upstream.has_fred_key:
        pytest.skip("FRED_API_KEY not set")
    out = services.get_series(
        ["CUSR0000SA0", "CPIAUCSL"], upstream, transform="pct_change_year_ago", max_points=12
    )
    assert {r.meta.source for r in out.results} == {"BLS", "FRED"}
    assert not out.failed


def test_release_schedule_for_bls_and_fred_series(upstream):
    """Phase 5: the release calendar resolves BLS IDs by prefix and FRED IDs via FRED."""
    for series_id, release_id in (("CES0500000003", 50), ("PAYEMS", 50), ("CUSR0000SA0", 10)):
        out = services.describe_series(series_id, upstream)
        assert out.release is not None and out.release.release_id == release_id
        assert out.release.last_release_date is not None
        assert out.release.next_release_date is not None
        assert out.release.next_release_date > out.release.last_release_date
