from datetime import datetime

import pandas as pd
import pytest

from econ_gateway.services.transforms import (
    apply_transform,
    infer_freq_code,
    period_label,
    transform_units,
    year_ago,
)


@pytest.mark.parametrize(
    ("freq", "expected"),
    [("D", "D"), ("W-SAT", "W"), ("MS", "M"), ("QS", "Q"), ("YS", "A")],
)
def test_infer_freq_code(freq, expected):
    assert infer_freq_code(pd.date_range("2020-01-01", periods=10, freq=freq)) == expected


def test_infer_freq_code_single_point_defaults_to_monthly():
    assert infer_freq_code(pd.DatetimeIndex(["2026-01-01"])) == "M"


@pytest.mark.parametrize(
    ("freq", "expected"),
    [("M", "2026-09"), ("Q", "2026-Q3"), ("S", "2026-H2"), ("A", "2026"), ("W", "2026-09-01")],
)
def test_period_label(freq, expected):
    assert period_label(datetime(2026, 9, 1), freq) == expected


def test_year_ago_is_date_based_not_row_based():
    # A missing month must not shift the comparison by one row.
    idx = pd.to_datetime(["2025-01-01", "2025-03-01", "2026-01-01", "2026-03-01"])
    s = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
    assert year_ago(s, "M").tolist()[2:] == [1.0, 2.0]


def test_year_ago_weekly_matches_nearest_within_tolerance():
    s = pd.Series(range(60), index=pd.date_range("2025-01-04", periods=60, freq="W-SAT"))
    prior = year_ago(s.astype(float), "W")
    # 2026-01-03 minus one year is 2025-01-03 (Friday); nearest week is 2025-01-04 (value 0).
    assert prior.loc[pd.Timestamp("2026-01-03")] == 0.0


def test_apply_transform_pct_change_year_ago():
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    s = pd.Series([100.0] * 12 + [103.0], index=idx)
    out = apply_transform(s, "pct_change_year_ago", "M")
    assert out.iloc[-1] == 3.0
    assert out.iloc[:12].isna().all()


def test_apply_transform_change_prior_period_rounds():
    s = pd.Series([4.1, 4.3], index=pd.date_range("2026-01-01", periods=2, freq="MS"))
    assert apply_transform(s, "change_prior_period", "M").iloc[-1] == 0.2


def test_level_returns_values_untouched():
    s = pd.Series([1.23456789], index=pd.DatetimeIndex(["2026-01-01"]))
    assert apply_transform(s, "level", "M") is s


def test_transform_units_for_rates_are_percentage_points():
    assert transform_units("change_year_ago", "percent", rate=True) == (
        "change from year ago (percentage points)"
    )
    assert transform_units("pct_change_prior_period", "index", rate=False) == (
        "percent change from prior period"
    )
