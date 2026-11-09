from datetime import date

import pytest

from conftest import TODAY, FakeUpstream
from econ_gateway import services
from econ_gateway.services.errors import InvalidRequestError, SeriesNotFoundError
from econ_gateway.services.observations import parse_date, resolve_window

# ---------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("text", "end", "expected"),
    [
        ("2019", False, date(2019, 1, 1)),
        ("2019", True, date(2019, 12, 31)),
        ("2024-02", True, date(2024, 2, 29)),
        ("2024-02-10", False, date(2024, 2, 10)),
    ],
)
def test_parse_date_expands_partial_dates(text, end, expected):
    assert parse_date(text, end=end) == expected


def test_parse_date_rejects_impossible_dates_with_example():
    with pytest.raises(InvalidRequestError, match="YYYY-MM"):
        parse_date("2024-13", end=False)


def test_resolve_window_defaults_and_caps_end_at_today():
    start, end = resolve_window(None, "2030", TODAY)
    assert start == date(2021, 1, 1)
    assert end == TODAY


def test_resolve_window_rejects_inverted_window():
    with pytest.raises(InvalidRequestError, match="after end_date"):
        resolve_window("2025", "2020", TODAY)


# ---------------------------------------------------------------- get_series


def test_get_series_concise_returns_points_and_provenance(upstream):
    out = services.get_series(
        ["lns14000000"], upstream, start_date="2026-01", max_points=60, today=TODAY
    )
    (series,) = out.results
    assert series.meta.series_id == "LNS14000000"
    assert series.meta.url == "https://data.bls.gov/timeseries/LNS14000000"
    assert series.points is not None and series.observations is None
    assert series.points[0][0] == "2026-01" and series.points[-1][0] == "2026-09"
    assert series.preliminary_periods == ["2026-09"]
    assert series.latest_available == "2026-09"
    assert not series.truncated


def test_get_series_truncates_to_most_recent_and_says_so(upstream):
    out = services.get_series(["CUSR0000SA0"], upstream, max_points=6, today=TODAY)
    (series,) = out.results
    assert series.returned == 6 and series.available > 6 and series.truncated
    assert series.points is not None and series.points[-1][0] == "2026-09"
    assert any("truncated" in n for n in out.notes)


def test_get_series_transform_fetches_extra_year_so_first_point_is_filled(upstream):
    out = services.get_series(
        ["CUSR0000SA0"],
        upstream,
        start_date="2025-06",
        transform="pct_change_year_ago",
        max_points=60,
        today=TODAY,
    )
    (series,) = out.results
    assert series.points is not None
    first_period, first_value = series.points[0]
    assert first_period == "2025-06" and first_value is not None
    assert series.value_units == "percent change from year ago"


def test_get_series_warns_on_percent_change_of_a_rate(upstream):
    out = services.get_series(
        ["LNS14000000"], upstream, transform="pct_change_year_ago", max_points=12, today=TODAY
    )
    assert any("percentage points" in n for n in out.notes)


def test_get_series_reports_failures_per_series(upstream):
    out = services.get_series(
        ["LNS14000000", "LNU99999999", "NOTAREALSERIES"], upstream, max_points=5, today=TODAY
    )
    assert [r.meta.series_id for r in out.results] == ["LNS14000000"]
    reasons = {f.id: f.reason for f in out.failed}
    assert "does not exist" in reasons["LNU99999999"]
    assert "FRED has no series" in reasons["NOTAREALSERIES"]
    assert all("search_series" in f.hint for f in out.failed)


def test_get_series_detailed_includes_footnotes_and_notes(upstream):
    out = services.get_series(
        ["ICSA"], upstream, max_points=3, response_format="detailed", today=TODAY
    )
    (series,) = out.results
    assert series.points is None and series.observations is not None
    assert series.meta.notes == "Source notes."
    assert series.meta.frequency == "weekly"
    assert series.observations[-1].date == "2026-10-31"


def test_get_series_caps_total_points_across_series(upstream, monkeypatch):
    monkeypatch.setattr("econ_gateway.services.observations.TOTAL_POINTS_LIMIT", 10)
    out = services.get_series(["LNS14000000", "CUSR0000SA0"], upstream, max_points=60, today=TODAY)
    assert all(r.returned == 5 for r in out.results)
    assert any("Capped at 5 points" in n for n in out.notes)


def test_get_series_batches_bls_ids_into_one_request(upstream):
    services.get_series(["LNS14000000", "CUSR0000SA0"], upstream, max_points=5, today=TODAY)
    assert [c for c in upstream.calls if c[0] == "fetch_bls"] == [
        ("fetch_bls", (("LNS14000000", "CUSR0000SA0"), 2021, 2026))
    ]


# ---------------------------------------------------------------- get_latest


def test_get_latest_rate_reports_percentage_points_not_percent(upstream):
    out = services.get_latest(["LNS14000000"], upstream, today=TODAY)
    (latest,) = out.results
    assert latest.latest_period == "2026-09" and latest.preliminary
    assert latest.previous_period == "2026-08"
    assert latest.year_ago_period == "2025-09"
    assert latest.change_from_year_ago == pytest.approx(0.12)
    assert latest.pct_change_from_year_ago is None
    assert latest.change_units == "percentage points"


def test_get_latest_index_reports_percent_change(upstream):
    out = services.get_latest(["CUSR0000SA0"], upstream, today=TODAY)
    (latest,) = out.results
    assert latest.latest_value == 333.0 and latest.year_ago_value == 321.0
    assert latest.pct_change_from_year_ago == pytest.approx(3.738)


def test_get_latest_weekly_year_ago_uses_nearest_week(upstream):
    out = services.get_latest(["ICSA"], upstream, today=TODAY)
    (latest,) = out.results
    assert latest.latest_period == "2026-10-31"
    assert latest.change_from_year_ago == 52_000.0


def test_get_latest_explains_a_missing_year_ago_value(upstream):
    # Like BLS October 2025 (not published during the shutdown): drop 2025-09 from the series.
    frame = upstream.bls["LNS14000000"]
    upstream.bls["LNS14000000"] = frame.loc[frame["date"] != "2025-09-01"]
    (latest,) = services.get_latest(["LNS14000000"], upstream, today=TODAY).results
    assert latest.year_ago_period == "2025-09" and latest.year_ago_value is None
    assert latest.change_from_year_ago is None
    note = latest.notes[0]
    assert "no published value for 2025-09" in note
    # The neighbours are named with their values, so a fallback comparison is labelled right.
    assert "2025-08 (" in note and "2025-10 (" in note
    assert "never present its value as 2025-09" in note


def test_get_latest_without_fred_key_fails_fred_series_only():
    upstream = FakeUpstream(fred_key=False)
    out = services.get_latest(["LNS14000000", "ICSA"], upstream, today=TODAY)
    assert [r.meta.series_id for r in out.results] == ["LNS14000000"]
    assert "FRED_API_KEY" in out.failed[0].reason


# ---------------------------------------------------------------- search_series


def test_search_prefers_catalog_and_skips_fred_when_catalog_covers_it(upstream):
    out = services.search_series("unemployment rate", upstream, geography="Texas", limit=5)
    assert out.results[0].series_id == "LASST480000000000003"
    assert out.results[0].match == "catalog"


def test_search_uses_fred_first_when_place_not_covered(upstream):
    out = services.search_series("average hourly earnings", upstream, geography="Texas", limit=5)
    assert out.fred_searched
    # Re-ranked: the statewide total beats the construction subset FRED listed first.
    assert [r.series_id for r in out.results[:2]] == [
        "SMU48000000500000003",
        "SMU48000002000000003",
    ]
    assert out.results[0].match == "fred_search"
    assert out.results[0].geography == "Texas"
    assert any("Texas" in n for n in out.notes)


def test_search_without_fred_key_explains_limitation():
    out = services.search_series(
        "average hourly earnings", FakeUpstream(fred_key=False), geography="Texas", limit=5
    )
    assert not out.fred_searched
    assert any("FRED_API_KEY" in n for n in out.notes)


def test_search_with_no_matches_suggests_better_terms(upstream):
    upstream.fred_search_results = upstream.fred_search_results.head(0)
    out = services.search_series("zzzz qqqq", upstream, limit=5)
    assert out.results == []
    assert any("standard terms" in n for n in out.notes)


# ---------------------------------------------------------------- describe_series


def test_describe_fred_catalog_series_merges_curated_meta_with_notes(upstream):
    out = services.describe_series("icsa", upstream, today=TODAY)
    assert out.in_catalog and out.meta.title == "Initial claims"
    assert out.meta.notes == "Source notes."
    assert out.observation_start == "1967-01-07"


def test_describe_bls_catalog_series_costs_no_api_calls(upstream):
    out = services.describe_series("CUSR0000SA0", upstream, today=TODAY)
    assert out.observation_end == "2026-09"
    assert upstream.calls == []


def test_describe_unknown_bls_series_raises_with_next_step(upstream):
    with pytest.raises(SeriesNotFoundError, match="search_series"):
        services.describe_series("LNU99999999", upstream, today=TODAY)


# ---------------------------------------------------------------- unpublished periods (gaps)


def _blank_month(upstream, series_id: str, month: str) -> None:
    """Like BLS: the row exists but its value is '-' (NaN), e.g. October 2025."""
    frame = upstream.bls[series_id].copy()
    frame.loc[frame["date"] == f"{month}-01", "value"] = float("nan")
    upstream.bls[series_id] = frame


def test_get_series_names_unpublished_periods(upstream):
    _blank_month(upstream, "LNS14000000", "2025-10")
    out = services.get_series(
        ["LNS14000000"], upstream, start_date="2025-06", max_points=60, today=TODAY
    )
    points = out.results[0].points
    assert points is not None and dict(points)["2025-10"] is None
    assert any("no published value for 2025-10" in n for n in out.notes)


def test_get_series_explains_changes_missing_their_comparison(upstream):
    _blank_month(upstream, "LNS14000000", "2025-09")
    out = services.get_series(
        ["LNS14000000"], upstream, start_date="2026-06", transform="change_year_ago",
        max_points=60, today=TODAY,
    )  # fmt: skip
    points = out.results[0].points
    assert points is not None and dict(points)["2026-09"] is None
    assert any("change_year_ago is null for 2026-09" in n for n in out.notes)
    # The gap itself lies before the window, so it is not reported as an unpublished period.
    assert not any("the source has no published value" in n for n in out.notes)


def test_get_series_flags_newer_data_cut_off_by_a_recent_end_date(upstream):
    out = services.get_series(
        ["LNS14000000"], upstream, start_date="2026-01", end_date="2026-06", max_points=60,
        today=TODAY,
    )  # fmt: skip
    data = out.results[0]
    assert data.points is not None and data.points[-1][0] == "2026-06"
    assert data.latest_available == "2026-09"
    assert any("newer data exists" in n and "2026-09" in n for n in out.notes)


def test_get_series_with_an_old_end_date_does_not_fetch_to_today(upstream):
    out = services.get_series(
        ["LNS14000000"], upstream, start_date="2020", end_date="2023-12", max_points=60,
        today=TODAY,
    )  # fmt: skip
    assert out.results[0].latest_available is None
    assert ("fetch_bls", (("LNS14000000",), 2020, 2023)) in upstream.calls
