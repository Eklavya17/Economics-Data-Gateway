"""Shared fixtures: an in-memory ``Upstream`` so tests never touch BLS or FRED."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

from econ_gateway.models import Source
from econ_gateway.resolve.retrieval import CatalogRetriever
from econ_gateway.services.errors import ConfigurationError, SeriesNotFoundError
from fake_places import FakePlacesMixin

TODAY = date(2026, 11, 7)
# Release dates around TODAY (FRED release IDs). CPI's next release is after TODAY.
RELEASE_DATES: dict[int, list[date]] = {
    50: [date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4)],
    10: [date(2026, 10, 14), date(2026, 11, 10)],
    180: [date(2026, 10, 29), date(2026, 11, 5), date(2026, 11, 12)],
}


def _catalog_row(
    series_id: str, source: str, title: str, frequency: str, units: str, seasonal: str,
    keywords: str, geography: str = "United States", industry: str = "",
) -> dict[str, str]:  # fmt: skip
    """One curated-catalog row with the columns the real catalog has."""
    return {
        "series_id": series_id, "source": source, "title": title, "category": "",
        "measure": "", "frequency": frequency, "units": units, "seasonal_adjustment": seasonal,
        "industry": industry, "geography": geography, "demographic": "", "keywords": keywords,
        "validated": "yes", "last_obs": "2026-09",
    }  # fmt: skip


CATALOG = pd.DataFrame(
    [
        _catalog_row("LNS14000000", "BLS", "Unemployment rate", "M", "percent", "SA",
                     "unemployment rate jobless"),
        _catalog_row("LASST480000000000003", "BLS", "Unemployment rate: Texas", "M", "percent",
                     "SA", "unemployment rate jobless", geography="Texas"),
        _catalog_row("CES0500000003", "BLS",
                     "Average hourly earnings of all employees, Total private", "M",
                     "dollars per hour", "SA", "wages pay earnings hourly",
                     industry="Total private"),
        _catalog_row("CES3000000003", "BLS",
                     "Average hourly earnings of all employees, Manufacturing", "M",
                     "dollars per hour", "SA", "wages pay earnings hourly",
                     industry="Manufacturing"),
        _catalog_row("CUSR0000SA0", "BLS", "CPI-U: All items", "M", "index 1982-84=100", "SA",
                     "inflation consumer prices cpi"),
        _catalog_row("CUUR0000SA0", "BLS", "CPI-U: All items (not seasonally adjusted)", "M",
                     "index 1982-84=100", "NSA", "inflation consumer prices cpi"),
        _catalog_row("CIU2020000000000I", "BLS",
                     "Employment Cost Index: wages and salaries, private industry", "Q",
                     "index Dec 2005=100", "NSA", "eci employment cost index wages"),
        _catalog_row("PCEPI", "FRED", "PCE price index", "M", "index 2017=100", "SA",
                     "pce prices inflation"),
        _catalog_row("ICSA", "FRED", "Initial claims", "W", "number", "SA",
                     "jobless claims unemployment insurance weekly"),
    ]
)  # fmt: skip
CATALOG["category"] = CATALOG["series_id"].map(
    lambda sid: {"LN": "unemployment", "LA": "unemployment", "CE": "wages", "CI": "wages",
                 "CU": "inflation", "PC": "inflation", "IC": "claims"}[sid[:2]]
)  # fmt: skip


def monthly_bls(series_id: str, values: list[float], end: str = "2026-09") -> pd.DataFrame:
    """BLS-shaped long frame of monthly values ending at ``end``; the last value is preliminary."""
    dates = pd.date_range(end=pd.Timestamp(f"{end}-01"), periods=len(values), freq="MS")
    footnotes = [""] * (len(values) - 1) + ["preliminary"]
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": dates,
            "year": dates.year,
            "period": [f"M{d.month:02d}" for d in dates],
            "period_name": [f"{d:%B}" for d in dates],
            "freq": "M",
            "value": values,
            "footnotes": footnotes,
            "source": "BLS",
        }
    )


def quarterly_bls(series_id: str, values: list[float], end: str = "2026-07") -> pd.DataFrame:
    """BLS-shaped quarterly frame (period Q01-Q04, dated at quarter start) ending at ``end``."""
    dates = pd.date_range(end=pd.Timestamp(f"{end}-01"), periods=len(values), freq="QS")
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": dates,
            "period": [f"Q0{(d.month - 1) // 3 + 1}" for d in dates],
            "freq": "Q",
            "value": values,
            "footnotes": "",
            "source": "BLS",
        }
    )


def fred_frame(series_id: str, values: list[float], end: str, freq: str) -> pd.DataFrame:
    """FRED-shaped frame with ``freq`` spacing ending at ``end``."""
    dates = pd.date_range(end=pd.Timestamp(end), periods=len(values), freq=freq)
    return pd.DataFrame({"series_id": series_id, "date": dates, "value": values, "source": "FRED"})


class FakeUpstream(FakePlacesMixin):
    """In-memory ``Upstream``: serves canned frames and records calls."""

    def __init__(self, *, fred_key: bool = True, bls_key: bool = True) -> None:
        self._fred_key, self._bls_key = fred_key, bls_key
        self._retriever = CatalogRetriever(CATALOG)
        # 34 months of history so year-ago comparisons work from a 2-year lookback.
        unemployment = [4.0 + 0.01 * i for i in range(34)]
        self.bls: dict[str, pd.DataFrame] = {
            "LNS14000000": monthly_bls("LNS14000000", unemployment),
            "CUSR0000SA0": monthly_bls("CUSR0000SA0", [300.0 + i for i in range(34)]),
            "CES0500000003": monthly_bls("CES0500000003", [30.0 + 0.1 * i for i in range(34)]),
            # Manufacturing pay grows 0.5%/month (~6.2%/yr), faster than the fake CPI.
            "CES3000000003": monthly_bls("CES3000000003", [30.0 * 1.005**i for i in range(34)]),
            "CUUR0000SA0": monthly_bls("CUUR0000SA0", [300.0 + i for i in range(34)]),
            # Quarterly ECI, NSA, through 2026-Q3 (dated 2026-07-01), +1%/quarter.
            "CIU2020000000000I": quarterly_bls(
                "CIU2020000000000I", [150.0 * 1.01**i for i in range(12)]
            ),
        }
        self.fred: dict[str, pd.DataFrame] = {
            # Weekly claims, week ending Saturday.
            "ICSA": fred_frame(
                "ICSA", [200_000.0 + 1000 * i for i in range(110)], "2026-10-31", "W-SAT"
            ),
            # FRED's copy of a BLS state series (Texas average hourly earnings).
            "SMU48000000500000003": fred_frame(
                "SMU48000000500000003", [33.0] * 3, "2026-09-01", "MS"
            ),
            "PCEPI": fred_frame("PCEPI", [120.0 + 0.2 * i for i in range(34)], "2026-09-01", "MS"),
        }
        self.fred_search_results = pd.DataFrame(
            [
                # FRED really returns these two tied on popularity, construction first.
                {
                    "id": "SMU48000002000000003",
                    "title": "Average Hourly Earnings of All Employees: Construction in Texas",
                    "frequency_short": "M",
                    "units_short": "$ per Hour",
                    "seasonal_adjustment_short": "NSA",
                    "observation_start": "2007-01-01",
                    "observation_end": "2026-09-01",
                    "popularity": 40,
                    "last_updated": "2026-10-17",
                },
                {
                    "id": "SMU48000000500000003",
                    "title": "Average Hourly Earnings of All Employees: Total Private in Texas",
                    "frequency_short": "M",
                    "units_short": "$ per Hour",
                    "seasonal_adjustment_short": "SA",
                    "observation_start": "2007-01-01",
                    "observation_end": "2026-09-01",
                    "popularity": 40,
                    "last_updated": "2026-10-17",
                },
            ]
        )
        self.calls: list[tuple[str, Any]] = []

    @property
    def has_fred_key(self) -> bool:
        return self._fred_key

    @property
    def has_bls_key(self) -> bool:
        return self._bls_key

    def catalog(self) -> pd.DataFrame:
        return CATALOG

    def retriever(self) -> CatalogRetriever:
        return self._retriever

    def infer_source(self, series_id: str) -> Source:
        hit = CATALOG.loc[CATALOG["series_id"] == series_id, "source"]
        if not hit.empty:
            return "BLS" if hit.iloc[0] == "BLS" else "FRED"
        return "BLS" if series_id.startswith(("LN", "CE", "CU", "CW", "CI", "LA")) else "FRED"

    def fetch_bls(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> tuple[pd.DataFrame, list[str]]:
        self.calls.append(("fetch_bls", (tuple(series_ids), start_year, end_year)))
        frames = [self.bls[s] for s in series_ids if s in self.bls]
        messages = [
            f"Series does not exist for Series {s}" for s in series_ids if s not in self.bls
        ]
        frame = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=["series_id", "date", "period", "value", "footnotes", "freq"])
        )
        frame["date"] = pd.to_datetime(frame["date"])
        in_years = (frame["date"].dt.year >= start_year) & (frame["date"].dt.year <= end_year)
        return frame.loc[in_years].reset_index(drop=True), messages

    def fetch_fred(self, series_id: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append(("fetch_fred", (series_id, start, end)))
        if not self._fred_key:
            raise ConfigurationError("FRED_API_KEY is not set on the gateway server.")
        if series_id not in self.fred:
            raise SeriesNotFoundError(f"FRED has no series '{series_id}'.")
        frame = self.fred[series_id]
        return frame.loc[(frame["date"] >= start) & (frame["date"] <= end)].reset_index(drop=True)

    def search_fred(self, text: str, limit: int) -> pd.DataFrame:
        self.calls.append(("search_fred", (text, limit)))
        return self.fred_search_results.head(limit)

    def fred_series_info(self, series_id: str) -> dict[str, Any]:
        self.calls.append(("fred_series_info", series_id))
        if series_id not in self.fred:
            raise SeriesNotFoundError(f"FRED has no series '{series_id}'.")
        return {
            "id": series_id,
            "title": f"Title of {series_id}",
            "frequency_short": "W" if series_id == "ICSA" else "M",
            "units": "Number" if series_id == "ICSA" else "Dollars per Hour",
            "seasonal_adjustment_short": "SA",
            "notes": "Source notes.",
            "observation_start": "1967-01-07",
            "observation_end": "2026-10-31",
            "last_updated": "2026-11-05",
        }

    def bls_series_info(self, series_id: str) -> dict[str, Any] | None:
        self.calls.append(("bls_series_info", series_id))
        return None

    def release_id(self, series_id: str, source: Source) -> int | None:
        if series_id.startswith(("CE", "LN")) or series_id == "PAYEMS":
            return 50  # Employment Situation
        if series_id.startswith("CU"):
            return 10  # Consumer Price Index
        return 180 if series_id == "ICSA" else None

    def release_dates(self, release_id: int) -> list[date]:
        return RELEASE_DATES.get(release_id, [])

    def status(self) -> dict[str, object]:
        return {"BLS": {"circuit": {"state": "closed"}}, "FRED": {"circuit": {"state": "closed"}}}


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()
