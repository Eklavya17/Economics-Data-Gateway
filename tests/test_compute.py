import pandas as pd
import pytest

from conftest import TODAY, monthly_bls
from econ_gateway import services
from econ_gateway.services.errors import InvalidRequestError, SeriesNotFoundError
from econ_gateway.services.transforms import to_frequency

# ---------------------------------------------------------------- frequency alignment


def test_to_frequency_keeps_only_complete_periods():
    monthly = pd.Series(
        [1.0, 2.0, 3.0, 4.0],
        index=pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]),
    )
    quarterly = to_frequency(monthly, "M", "Q")
    assert quarterly is not None
    assert quarterly.to_dict() == {pd.Timestamp("2026-01-01"): 2.0}  # Q2 has one month: dropped


def test_to_frequency_refuses_disaggregation():
    s = pd.Series([1.0], index=pd.to_datetime(["2026-01-01"]))
    assert to_frequency(s, "Q", "M") is None


# ---------------------------------------------------------------- real_wage_growth


def test_real_wage_growth_monthly_uses_exact_formula(upstream):
    out = services.real_wage_growth("CES3000000003", upstream, max_points=24, today=TODAY)
    assert out.deflator.series_id == "CUSR0000SA0"  # SA wage -> SA CPI
    latest = out.latest
    assert latest is not None and latest.period == "2026-09"
    nominal = 1.005**12 - 1  # fake wage grows 0.5% a month
    inflation = 333 / 321 - 1  # fake CPI is 300 + i
    assert latest.nominal_pct_change_year_ago == pytest.approx(nominal * 100, abs=1e-3)
    assert latest.inflation_pct_change_year_ago == pytest.approx(inflation * 100, abs=1e-3)
    assert latest.real_pct_change_year_ago == pytest.approx(
        ((1 + nominal) / (1 + inflation) - 1) * 100, abs=1e-3
    )
    assert out.real_wage_rising is True
    # Real wage is in base-period dollars, so it equals the nominal wage in the base period.
    assert out.base_period == "2026-09" and latest.real_wage == latest.nominal_wage


def test_real_wage_growth_quarterly_nsa_matches_deflator_and_frequency(upstream):
    out = services.real_wage_growth("CIU2020000000000I", upstream, max_points=8, today=TODAY)
    assert out.deflator.series_id == "CUUR0000SA0"  # NSA wage -> NSA CPI
    assert out.frequency == "quarterly"
    assert out.latest is not None and out.latest.period == "2026-Q3"
    # Q3 CPI mean = mean(331, 332, 333) = 332; a year earlier mean(319, 320, 321) = 320.
    assert out.latest.inflation_pct_change_year_ago == pytest.approx(
        (332 / 320 - 1) * 100, abs=1e-3
    )
    assert out.latest.nominal_pct_change_year_ago == pytest.approx((1.01**4 - 1) * 100, abs=1e-3)
    assert any("averaged to quarterly" in n for n in out.notes)
    assert any("not seasonally adjusted" in n for n in out.notes)


def test_real_wage_growth_window_change(upstream):
    out = services.real_wage_growth(
        "CES3000000003", upstream, start_date="2025-09", max_points=24, today=TODAY
    )
    window = out.window
    assert window is not None and (window.start_period, window.end_period) == ("2025-09", "2026-09")
    assert window.inflation_pct_change == pytest.approx((333 / 321 - 1) * 100, abs=1e-3)


def test_real_wage_growth_rejects_rates(upstream):
    with pytest.raises(InvalidRequestError, match="not a wage level"):
        services.real_wage_growth("LNS14000000", upstream, max_points=5, today=TODAY)


def test_real_wage_growth_pce_with_nsa_wage_explains_mismatch(upstream):
    out = services.real_wage_growth(
        "CIU2020000000000I", upstream, deflator="pce", max_points=4, today=TODAY
    )
    assert out.deflator.series_id == "PCEPI"
    assert any("PCE is only published seasonally adjusted" in n for n in out.notes)


def test_real_wage_growth_flags_wages_ahead_of_prices(upstream):
    upstream.bls["CES3000000003"] = monthly_bls(
        "CES3000000003", [30.0 * 1.005**i for i in range(34)], end="2026-10"
    )
    out = services.real_wage_growth("CES3000000003", upstream, max_points=5, today=TODAY)
    assert out.base_period == "2026-09"
    assert any("published through 2026-10" in n for n in out.notes)


def test_real_wage_growth_unknown_series(upstream):
    with pytest.raises(SeriesNotFoundError, match="search_series"):
        services.real_wage_growth("CES9999999999", upstream, max_points=5, today=TODAY)


# ---------------------------------------------------------------- compare_series


def test_compare_ranks_and_measures_gap_vs_benchmark(upstream):
    out = services.compare_series(
        ["CES0500000003", "CES3000000003"],
        upstream,
        measure="pct_change_year_ago",
        benchmark_series_id="CUSR0000SA0",
        today=TODAY,
    )
    assert [r.meta.series_id for r in out.rows] == ["CES3000000003", "CES0500000003"]
    assert [r.rank for r in out.rows] == [1, 2]
    top = out.rows[0]
    assert out.benchmark is not None and out.benchmark.value is not None
    assert top.value is not None and top.gap_vs_benchmark == pytest.approx(
        top.value - out.benchmark.value, abs=1e-3
    )
    assert top.value_units == "percent" and top.base_period == "2025-09"


def test_compare_evaluates_rows_at_the_benchmarks_latest_period(upstream):
    # Live regression: October wages were compared with September CPI.
    upstream.bls["CES3000000003"] = monthly_bls(
        "CES3000000003", [30.0 * 1.005**i for i in range(34)], end="2026-10"
    )
    out = services.compare_series(
        ["CES3000000003", "CES0500000003"],
        upstream,
        measure="pct_change_year_ago",
        benchmark_series_id="CUSR0000SA0",
        today=TODAY,
    )
    assert {r.latest_period for r in out.rows} == {"2026-09"}
    assert any("benchmark's latest period" in n for n in out.notes)


def test_real_wage_method_matches_project_1_on_identical_inputs(upstream):
    # Project 1's verified run (2026-11-06) used Sep 2025 $35.70 -> Sep 2026 $36.92 manufacturing
    # pay and CPI 324.245 -> 336.298, reporting +3.4174% nominal, +3.7173% CPI, -0.2891% real.
    # BLS has since revised Sep 2026 pay to $37.06; on the same inputs the methods must agree.
    wages = [35.70] + [36.0] * 11 + [36.92]
    prices = [324.245] + [330.0] * 11 + [336.298]
    upstream.bls["CES3000000003"] = monthly_bls("CES3000000003", wages)
    upstream.bls["CUSR0000SA0"] = monthly_bls("CUSR0000SA0", prices)
    out = services.real_wage_growth("CES3000000003", upstream, max_points=1, today=TODAY)
    assert out.latest is not None
    assert out.latest.nominal_pct_change_year_ago == pytest.approx(3.417, abs=1e-3)
    assert out.latest.inflation_pct_change_year_ago == pytest.approx(3.717, abs=1e-3)
    assert out.latest.real_pct_change_year_ago == pytest.approx(-0.289, abs=1e-3)
    assert out.real_wage_rising is False


def test_compare_leaves_out_percent_change_of_rates(upstream):
    out = services.compare_series(
        ["LNS14000000", "CES0500000003"], upstream, measure="pct_change_year_ago", today=TODAY
    )
    unemployment = next(r for r in out.rows if r.meta.series_id == "LNS14000000")
    assert unemployment.value is None and unemployment.rank is None
    assert out.rows[-1] is unemployment
    assert any("percentage points" in n for n in out.notes)


def test_compare_since_year_uses_that_years_average(upstream):
    out = services.compare_series(
        ["CUSR0000SA0", "CUUR0000SA0"], upstream, measure="change_since", since="2025", today=TODAY
    )
    row = out.rows[0]
    # Fake CPI in 2025 runs 313..324 (Jan..Dec), average 318.5.
    assert row.base_period == "2025 average" and row.base_value == pytest.approx(318.5)
    assert row.value == pytest.approx(333 - 318.5)


def test_compare_since_requires_since(upstream):
    with pytest.raises(InvalidRequestError, match="needs since"):
        services.compare_series(
            ["CUSR0000SA0", "CUUR0000SA0"], upstream, measure="pct_change_since", today=TODAY
        )


def test_compare_flags_incomparable_units_and_periods(upstream):
    out = services.compare_series(
        ["CES0500000003", "CUSR0000SA0", "ICSA"], upstream, measure="latest_value", today=TODAY
    )
    assert any("different units" in n for n in out.notes)
    assert any("different periods" in n for n in out.notes)  # ICSA is weekly, ends 2026-10-31


def test_compare_reports_unknown_ids(upstream):
    out = services.compare_series(
        ["CES0500000003", "NOPE1234"], upstream, measure="latest_value", today=TODAY
    )
    assert [f.id for f in out.failed] == ["NOPE1234"]
