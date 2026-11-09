"""Unit tests for services/gaps.py (unpublished periods and their notes)."""

from datetime import date, datetime

import pandas as pd

from econ_gateway.services import gaps


def _monthly(values: list[float], start: str = "2025-07") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


def test_unpublished_periods_ignores_leading_and_trailing_gaps():
    nan = float("nan")
    values = _monthly([nan, 4.1, nan, 4.3, nan])  # 2025-07 .. 2025-11
    assert gaps.unpublished_periods(values, "M") == ["2025-09"]
    assert gaps.unpublished_periods(values, "M", start=date(2025, 10, 1)) == []


def test_nearest_published_neighbours():
    values = _monthly([4.0, 4.1, float("nan"), 4.3])
    before, after = gaps.nearest_published(values, datetime(2025, 9, 1), "M")
    assert before == ("2025-08", 4.1) and after == ("2025-10", 4.3)


def test_missing_year_ago_note_names_gap_and_neighbours():
    values = _monthly([4.0, 4.1, float("nan"), 4.3])
    note = gaps.missing_year_ago_note(values, datetime(2025, 9, 1), "M")
    assert "no published value for 2025-09" in note
    assert "2025-08 (4.1) and 2025-10 (4.3)" in note


def test_missing_comparisons_skip_the_start_of_a_series():
    raw = _monthly([float(v) for v in range(1, 16)], start="2025-01")  # 2025-01 .. 2026-03
    transformed = raw - raw.shift(12)  # null for the first 12 months: no history, not a gap
    found = gaps.missing_comparisons(
        raw, transformed, "M", date(2025, 1, 1), date(2026, 3, 1), year_ago=True
    )
    assert found == []
    transformed.loc[pd.Timestamp("2026-02-01")] = float("nan")  # a real missing comparison
    found = gaps.missing_comparisons(
        raw, transformed, "M", date(2025, 1, 1), date(2026, 3, 1), year_ago=True
    )
    assert found == ["2026-02"]


def test_long_lists_are_summarized():
    note = gaps.unpublished_note("X", [f"2020-{m:02d}" for m in range(1, 11)])
    assert "and 4 more" in note
