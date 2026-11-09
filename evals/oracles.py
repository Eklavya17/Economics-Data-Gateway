"""Live oracles: compute the right answer straight from the data-source clients.

Oracles deliberately avoid the gateway's services. If an oracle reused ``get_latest`` or
``real_wage_growth``, a bug there would be graded as correct. They use the plain
``get_series`` / ``load_oews`` / ``fetch_qcew`` and plain pandas arithmetic, and FRED's release
calendar over plain HTTP.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from functools import lru_cache
from typing import cast

import pandas as pd
import requests

from econ_gateway.sources import fetch_qcew, get_series, load_oews

MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
]  # fmt: skip
ORDINALS = ["first", "second", "third", "fourth"]
FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"


# ---------------------------------------------------------------- period spellings


def month_variants(day: date | datetime) -> list[str]:
    """Ways an answer may name a month: 'September 2026', 'Sep 2026', '2026-09', '9/2026'."""
    name, year, month = MONTHS[day.month - 1], day.year, day.month
    variants = [f"{name} {year}", f"{name[:3]} {year}", f"{name[:3]}. {year}", f"{name}, {year}"]
    if name == "September":
        variants += [f"Sept {year}", f"Sept. {year}"]
    return variants + [f"{year}-{month:02d}", f"{month}/{year}", f"{month:02d}/{year}"]


def quarter_variants(year: int, quarter: int) -> list[str]:
    """Ways an answer may name a quarter: '2026-Q1', 'Q1 2026', 'first quarter of 2026'."""
    ordinal = ORDINALS[quarter - 1]
    return [
        f"{year}-Q{quarter}", f"Q{quarter} {year}", f"{year} Q{quarter}", f"Q{quarter}-{year}",
        f"{ordinal} quarter of {year}", f"{ordinal} quarter {year}", f"Q{quarter} of {year}",
        f"{ordinal} quarter, {year}",
    ]  # fmt: skip


def day_variants(day: date | datetime) -> list[str]:
    """Ways an answer may name a date: 'November 12', 'Nov 12', 'Nov. 12', '2026-11-12', '11/12'."""
    name = MONTHS[day.month - 1]
    return [
        f"{name} {day.day}", f"{name[:3]} {day.day}", f"{name[:3]}. {day.day}",
        f"{day:%Y-%m-%d}", f"{day.month}/{day.day}", f"{day.day} {name}",
    ]  # fmt: skip


# ---------------------------------------------------------------- data access


class Oracle:
    """Cached access to official data for computing expected answers.

    Args:
        today: Reference date (questions say "latest", "next", "past year").
    """

    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    @lru_cache(maxsize=64)  # noqa: B019  (one Oracle per run; caching per instance is intended)
    def series(self, series_id: str, start: int = 2020) -> pd.Series:
        """Monthly/weekly/quarterly values of one series, indexed by period date, NaN dropped."""
        frame = get_series([series_id], start)
        frame = frame.loc[frame["series_id"] == series_id]
        if "freq" in frame.columns:  # BLS frames carry annual averages (freq "A"); FRED's don't
            frame = frame.loc[frame["freq"] != "A"]
        values = pd.Series(
            frame["value"].to_numpy(dtype=float), index=pd.DatetimeIndex(frame["date"])
        )
        return values.sort_index().dropna()

    def last_date(self, series_id: str) -> pd.Timestamp:
        """Date of the latest published value."""
        # NaN values were dropped in series(), so the last index entry is a real date.
        return cast("pd.Timestamp", self.series(series_id).index[-1])

    def value_at(self, series_id: str, when: pd.Timestamp) -> float:
        """Value for the period starting at ``when`` (KeyError if not published)."""
        return float(self.series(series_id).at[when])

    def latest(self, series_id: str) -> tuple[pd.Timestamp, float]:
        """Latest period and value."""
        when = self.last_date(series_id)
        return when, self.value_at(series_id, when)

    def change_from_previous(self, series_id: str) -> tuple[pd.Timestamp, float]:
        """Latest period and its change from the period before, in the series units."""
        values = self.series(series_id)
        return self.last_date(series_id), float(values.iloc[-1]) - float(values.iloc[-2])

    def change_over_year(self, series_id: str) -> tuple[pd.Timestamp, float]:
        """Latest period and its change from a year earlier, in the series units."""
        when = self.last_date(series_id)
        year_ago = when - pd.DateOffset(years=1)
        return when, self.value_at(series_id, when) - self.value_at(series_id, year_ago)

    def yoy_pct(self, series_id: str, at: pd.Timestamp | None = None) -> tuple[pd.Timestamp, float]:
        """Percent change over 12 months at ``at`` (default: the latest period)."""
        when = at if at is not None else self.last_date(series_id)
        year_ago = when - pd.DateOffset(years=1)
        return when, (self.value_at(series_id, when) / self.value_at(series_id, year_ago) - 1) * 100

    @lru_cache(maxsize=4)  # noqa: B019
    def oews(self, level: str) -> tuple[int, pd.DataFrame]:
        """Newest published OEWS table at ``level`` and its May reference year."""
        for year in range(self.today.year - 1, self.today.year - 4, -1):
            try:
                return year, load_oews(year, level)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001  (not published yet: try the year before)
                continue
        raise RuntimeError("No OEWS table found in the last three years.")

    def oews_median(self, level: str, area: str, occ_code: str) -> tuple[int, float]:
        """May reference year and annual median wage for one occupation in one area."""
        year, table = self.oews(level)
        rows = table.loc[(table["area"] == area) & (table["occ_code"] == occ_code)]
        return year, float(rows["a_median"].to_numpy(dtype=float)[0])

    def qcew_total(self, area: str) -> tuple[int, int, int]:
        """Latest published QCEW quarter for ``area``: (year, quarter, total covered jobs)."""
        year, quarter = self.today.year, (self.today.month - 1) // 3 + 1
        for _ in range(8):
            quarter -= 1
            if quarter == 0:
                year, quarter = year - 1, 4
            try:
                frame = fetch_qcew(year, quarter, area=area)
            except Exception:  # noqa: BLE001  (not published yet: step back a quarter)
                continue
            total = frame.loc[
                (frame["own_code"].astype(str) == "0")
                & (frame["industry_code"].astype(str) == "10")
            ]
            if not total.empty:
                return year, quarter, int(total["month3_emplvl"].iloc[0])
        raise RuntimeError(f"No QCEW quarter found for {area}.")

    @lru_cache(maxsize=16)  # noqa: B019
    def next_release(self, release_id: int) -> date:
        """Next scheduled date of a FRED release after today."""
        response = requests.get(
            FRED_RELEASE_DATES,
            params={
                "release_id": release_id,
                "realtime_start": self.today.isoformat(),
                "realtime_end": "9999-12-31",
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "api_key": os.environ["FRED_API_KEY"],
                "file_type": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        dates = [date.fromisoformat(d["date"]) for d in response.json()["release_dates"]]
        return next(d for d in dates if d > self.today)
