"""In-memory OEWS / QCEW / area data for tests, shaped like the real tables.

Deliberate edge cases:
- a 7-digit OEWS nonmetropolitan area and a QCEW "Unknown" county (both must be skipped);
- a top-coded wage (Software Developers in Chicago) that must rank first;
- an annual-only occupation (teachers) and a missing estimate (teachers in Austin);
- a suppressed QCEW cell (construction, disclosure code N, zeros in the file);
- a QCEW area listed twice (the real reference table repeats ~4,600 codes).
"""

from __future__ import annotations

import math

import pandas as pd

from econ_gateway.services.errors import DataNotAvailableError

OEWS_YEAR = 2025
QCEW_LATEST = (2026, 1)

OEWS_AREAS = pd.DataFrame(
    [
        ("99", "U.S.", "1", "US", "national"),
        ("48", "Texas", "2", "TX", "state"),
        ("17", "Illinois", "2", "IL", "state"),
        ("12420", "Austin-Round Rock-San Marcos, TX", "4", "TX", "metro"),
        ("16980", "Chicago-Naperville-Elgin, IL-IN", "4", "IL", "metro"),
        ("4800001", "Big Thicket Region of Texas nonmetropolitan area", "6", "TX", "metro"),
    ],
    columns=["area", "area_title", "area_type", "prim_state", "level"],
)

QCEW_AREAS = pd.DataFrame(
    [
        ("US000", "U.S. TOTAL"),
        ("48000", "Texas -- Statewide"),
        ("17000", "Illinois -- Statewide"),
        ("C1242", "Austin-Round Rock-San Marcos, TX MSA"),
        ("C1698", "Chicago-Naperville-Elgin, IL-IN MSA"),
        ("48453", "Travis County, Texas"),
        ("48015", "Austin County, Texas"),
        ("48015", "Austin County, Texas"),  # the real table repeats codes
        ("48999", "Unknown Or Undefined, Texas"),
    ],
    columns=["area_fips", "area_title"],
)

OCCUPATIONS = pd.DataFrame(
    [
        ("00-0000", "All Occupations", "total"),
        ("29-0000", "Healthcare Practitioners and Technical Occupations", "major"),
        ("29-1141", "Registered Nurses", "detailed"),
        ("15-1252", "Software Developers", "detailed"),
        ("25-2021", "Elementary School Teachers, Except Special Education", "detailed"),
    ],
    columns=["occ_code", "occ_title", "o_group"],
)

_TITLES = dict(zip(OCCUPATIONS["occ_code"], OCCUPATIONS["occ_title"], strict=True))
_WAGE_COLS = [
    "h_mean",
    "a_mean",
    "h_median",
    "a_median",
    "a_pct10",
    "a_pct25",
    "a_pct75",
    "a_pct90",
]


def _oews_row(
    area: str, area_title: str, occ: str, a_median: float, *, capped: bool = False,
    annual_only: bool = False,
) -> dict[str, object]:  # fmt: skip
    """One cleaned OEWS row; wages derived from the annual median for simplicity."""
    row: dict[str, object] = {
        "area": area,
        "area_title": area_title,
        "i_group": "cross-industry",
        "own_code": "1235",
        "occ_code": occ,
        "occ_title": _TITLES[occ],
        "tot_emp": 10_000.0,
        "jobs_1000": 12.5,
        "loc_quotient": 1.1,
        "h_mean": a_median / 2080,
        "a_mean": a_median * 1.05,
        "h_median": a_median / 2080,
        "a_median": a_median,
        "a_pct10": a_median * 0.6,
        "a_pct25": a_median * 0.8,
        "a_pct75": a_median * 1.2,
        "a_pct90": a_median * 1.5,
        "annual": annual_only,
        "hourly": False,
    }
    for col in _WAGE_COLS:
        row[f"{col}_capped"] = False
    if annual_only:
        row["h_mean"] = row["h_median"] = math.nan
    if capped:
        for col in ("a_median", "a_pct75", "a_pct90"):
            row[col] = math.nan
            row[f"{col}_capped"] = True
    return row


OEWS_TABLES: dict[str, pd.DataFrame] = {
    "national": pd.DataFrame(
        [
            _oews_row("99", "U.S.", "29-1141", 97_550),
            _oews_row("99", "U.S.", "15-1252", 133_080),
            _oews_row("99", "U.S.", "25-2021", 64_000, annual_only=True),
        ]
    ),
    "state": pd.DataFrame(
        [
            _oews_row("48", "Texas", "29-1141", 89_000),
            _oews_row("17", "Illinois", "29-1141", 92_000),
            _oews_row("48", "Texas", "25-2021", 60_000, annual_only=True),
        ]
    ),
    "metro": pd.DataFrame(
        [
            _oews_row("12420", "Austin-Round Rock-San Marcos, TX", "29-1141", 90_000),
            _oews_row("16980", "Chicago-Naperville-Elgin, IL-IN", "29-1141", 95_000),
            _oews_row("12420", "Austin-Round Rock-San Marcos, TX", "15-1252", 150_000),
            _oews_row("16980", "Chicago-Naperville-Elgin, IL-IN", "15-1252", 0, capped=True),
        ]
    ),
}


def qcew_frame(area_fips: str) -> pd.DataFrame:
    """A QCEW area file with total, private, a suppressed sector and health care."""
    columns = [
        "area_fips", "own_code", "industry_code", "disclosure_code", "oty_disclosure_code",
        "qtrly_estabs", "month3_emplvl", "avg_wkly_wage", "oty_month3_emplvl_chg",
        "oty_month3_emplvl_pct_chg", "oty_avg_wkly_wage_pct_chg",
    ]  # fmt: skip
    rows = [
        (area_fips, "0", "10", None, None, 50_881, 931_218, 2087.0, 900, 0.1, 5.4),
        (area_fips, "5", "10", None, None, 50_499, 791_963, 2152.0, 3000, 0.4, 5.0),
        (area_fips, "5", "1012", "N", "N", 3, 0, 0.0, 0, 0.0, 0.0),
        (area_fips, "5", "62", None, None, 5044, 100_793, 1435.0, 3100, 3.2, -0.3),
    ]
    return pd.DataFrame(rows, columns=columns)


class FakePlacesMixin:
    """Place methods of the ``Upstream`` protocol, backed by the tables above."""

    calls: list[tuple[str, object]]

    def oews_areas(self) -> pd.DataFrame:
        return OEWS_AREAS

    def qcew_areas(self) -> pd.DataFrame:
        return QCEW_AREAS

    def occupations(self) -> pd.DataFrame:
        return OCCUPATIONS

    def oews(self, year: int, level: str) -> pd.DataFrame:
        self.calls.append(("oews", (year, level)))
        if year != OEWS_YEAR:
            raise DataNotAvailableError(f"OEWS May {year} ({level}) is not available.")
        return OEWS_TABLES[level]

    def qcew_area(self, year: int, quarter: int, area_code: str) -> pd.DataFrame:
        self.calls.append(("qcew_area", (year, quarter, area_code)))
        if (year, quarter) > QCEW_LATEST:
            raise DataNotAvailableError(f"QCEW {year}-Q{quarter} is not published.")
        return qcew_frame(area_code)
