"""Eval questions, each with a live oracle that computes the expected answer.

Split (Anthropic eval guidance, RESEARCH.md §3):

- **dev** (14): used while improving tool descriptions and responses.
- **held_out** (8): run only to check that improvements generalize. Do not tune against these.

Questions are phrased the way a person would ask, without series IDs or tool names, and cover
national, state, metro and county data, occupations, real wages, comparisons, the release
calendar, and questions whose honest answer is "not available".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from evals.grader import Expect, Expectation
from evals.oracles import Oracle, day_variants, month_variants, quarter_variants

Split = Literal["dev", "held_out"]


@dataclass(frozen=True)
class Case:
    """One eval question.

    Attributes:
        case_id: Stable identifier.
        split: ``dev`` or ``held_out``.
        category: Topic group for reports.
        question: What the user asks.
        oracle: Computes the expectation from live data.
        expected_tools: Tools a good run probably uses (reported, never graded).
    """

    case_id: str
    split: Split
    category: str
    question: str
    oracle: Callable[[Oracle], Expectation]
    expected_tools: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------- oracle helpers

RATE_TOL = 0.05  # percentage points: rates are published to one decimal
GROWTH_TOL = 0.1  # percent: growth rates may be rounded to one decimal
LEVEL_REL_TOL = 0.005  # 0.5%: levels may be rounded ("931 thousand")


def _latest_rate(series_id: str, what: str) -> Callable[[Oracle], Expectation]:
    def oracle(o: Oracle) -> Expectation:
        when, value = o.latest(series_id)
        return Expectation(
            numbers=[Expect(what, [value], abs_tol=RATE_TOL)],
            periods=[month_variants(when)],
            truth=f"{what}: {value}% ({when:%B %Y})",
        )

    return oracle


def _yoy(series_id: str, what: str) -> Callable[[Oracle], Expectation]:
    def oracle(o: Oracle) -> Expectation:
        when, pct = o.yoy_pct(series_id)
        return Expectation(
            numbers=[Expect(what, [round(pct, 2)], abs_tol=GROWTH_TOL)],
            periods=[month_variants(when)],
            truth=f"{what}: {pct:.2f}% over the year to {when:%B %Y}",
        )

    return oracle


def _oews_median(level: str, area: str, occ: str, what: str) -> Callable[[Oracle], Expectation]:
    def oracle(o: Oracle) -> Expectation:
        year, median = o.oews_median(level, area, occ)
        return Expectation(
            numbers=[Expect(what, [median], rel_tol=0.002)],
            periods=[[f"May {year}", str(year)]],
            truth=f"{what}: ${median:,.0f} (May {year})",
        )

    return oracle


def _next_release(release_id: int, name: str) -> Callable[[Oracle], Expectation]:
    def oracle(o: Oracle) -> Expectation:
        day = o.next_release(release_id)
        return Expectation(periods=[day_variants(day)], truth=f"next {name}: {day:%B %d, %Y}")

    return oracle


def _payrolls(o: Oracle) -> Expectation:
    when, change = o.change_from_previous("CES0000000001")  # thousands of jobs
    return Expectation(
        numbers=[
            Expect(
                "payroll change", [change, change * 1000], abs_tol=1, rel_tol=0.01, sign_free=True
            )
        ],
        periods=[month_variants(when)],
        truth=f"payrolls changed by {change:+.0f}K in {when:%B %Y}",
    )


def _cpi(o: Oracle) -> Expectation:
    when, nsa = o.yoy_pct("CUUR0000SA0")  # the headline 12-month rate is unadjusted
    _, sa = o.yoy_pct("CUSR0000SA0", when)
    return Expectation(
        numbers=[Expect("CPI 12-month change", [round(nsa, 2), round(sa, 2)], abs_tol=GROWTH_TOL)],
        periods=[month_variants(when)],
        truth=f"CPI-U up {nsa:.2f}% over the year to {when:%B %Y}",
    )


def _real_manufacturing_wages(o: Oracle) -> Expectation:
    # CPI comes out about a week after the jobs data: compare at the last month both have.
    when = min(o.last_date("CES3000000003"), o.last_date("CUSR0000SA0"))
    _, nominal = o.yoy_pct("CES3000000003", when)
    _, inflation = o.yoy_pct("CUSR0000SA0", when)
    real = ((1 + nominal / 100) / (1 + inflation / 100) - 1) * 100
    up = ["kept up", "outpac", "faster than", "ahead of", "exceed", "rose", "grew", "gain"]
    down = ["not kept", "haven't kept", "has not kept", "didn't keep", "did not keep",
            "fell behind", "lag", "slower than", "declin", "fell", "behind"]  # fmt: skip
    return Expectation(
        numbers=[
            Expect("nominal growth", [round(nominal, 2)], abs_tol=GROWTH_TOL),
            Expect("CPI inflation", [round(inflation, 2)], abs_tol=GROWTH_TOL),
        ],
        keywords=[up if real > 0 else down],
        truth=(f"{when:%B %Y}: wages +{nominal:.2f}%, CPI +{inflation:.2f}%, real {real:+.2f}%"),
    )


def _claims(o: Oracle) -> Expectation:
    when, value = o.latest("ICSA")
    return Expectation(
        numbers=[Expect("initial claims", [value, value / 1000], rel_tol=LEVEL_REL_TOL)],
        periods=[day_variants(when)],
        truth=f"{value:,.0f} initial claims, week ending {when:%B %d, %Y}",
    )


def _travis_jobs(o: Oracle) -> Expectation:
    year, quarter, jobs = o.qcew_total("48453")
    return Expectation(
        numbers=[Expect("covered jobs", [jobs, jobs / 1000], rel_tol=LEVEL_REL_TOL)],
        periods=[quarter_variants(year, quarter)],
        truth=f"Travis County: {jobs:,} jobs in {year}-Q{quarter}",
    )


def _two_industries(o: Oracle) -> Expectation:
    when, construction = o.yoy_pct("CES2000000003")
    _, manufacturing = o.yoy_pct("CES3000000003", when)
    faster = "construction" if construction > manufacturing else "manufacturing"
    return Expectation(
        numbers=[
            Expect("construction growth", [round(construction, 2)], abs_tol=GROWTH_TOL),
            Expect("manufacturing growth", [round(manufacturing, 2)], abs_tol=GROWTH_TOL),
        ],
        truth=(
            f"{when:%B %Y}: construction {construction:.2f}%, manufacturing "
            f"{manufacturing:.2f}% ({faster} faster)"
        ),
    )


def _future_abstain(o: Oracle) -> Expectation:
    return Expectation(abstain=True, truth="December 2027 data does not exist yet")


def _job_openings(o: Oracle) -> Expectation:
    when, value = o.latest("JTS000000000000000JOL")  # thousands
    return Expectation(
        numbers=[Expect("job openings", [value, value * 1000], rel_tol=LEVEL_REL_TOL)],
        periods=[month_variants(when)],
        truth=f"{value:,.0f}K job openings ({when:%B %Y})",
    )


def _county_nurse_wage(o: Oracle) -> Expectation:
    year, median = o.oews_median("metro", "12420", "29-1141")
    return Expectation(
        numbers=[Expect("Austin metro RN median", [median], rel_tol=0.002)],
        keywords=[["metro", "metropolitan", "msa"], ["austin"]],
        truth=f"OEWS has no county data; Austin metro RN median ${median:,.0f} (May {year})",
    )


GAP_PHRASES = [
    "not published", "no data", "not available", "unavailable", "missing", "shutdown",
    "wasn't published", "was not published", "not released", "never published",
    "not been published", "hasn't been published", "has not been published",
    "not been released", "unpublished",
]  # fmt: skip


def _rate_change_over_year(series_id: str, what: str) -> Callable[[Oracle], Expectation]:
    """Change in a rate over the year to the latest month, in percentage points.

    When the year-ago month was never published (October 2025, federal shutdown), the honest
    answer says so instead of silently comparing a different month; the oracle then expects a
    gap phrase and the latest month.
    """

    def oracle(o: Oracle) -> Expectation:
        try:
            when, change = o.change_over_year(series_id)
        except KeyError:
            latest = o.last_date(series_id)
            return Expectation(
                periods=[month_variants(latest)],
                keywords=[GAP_PHRASES],
                truth=(
                    f"{what}: the latest month is {latest:%B %Y}, but its year-ago month was "
                    "never published; the answer must say so"
                ),
            )
        return Expectation(
            numbers=[Expect("change", [round(change, 2)], abs_tol=RATE_TOL, sign_free=True)],
            keywords=[["percentage point", "point"]],
            truth=f"{what} changed {change:+.1f} pp over the year to {when:%B %Y}",
        )

    return oracle


def _top_metro_rn(o: Oracle) -> Expectation:
    year, table = o.oews("metro")
    rows = table.loc[table["occ_code"] == "29-1141"]
    # Medians above the OEWS cap are published only as "at or above"; any of those may lead.
    if "a_median_capped" in rows.columns:
        capped = rows.loc[rows["a_median_capped"].fillna(False).astype(bool)]
    else:
        capped = rows.iloc[0:0]
    leaders = capped if not capped.empty else rows.nlargest(1, "a_median")
    cities = sorted({str(t).split("-")[0].split(",")[0] for t in leaders["area_title"]})
    return Expectation(
        keywords=[cities],
        periods=[[f"May {year}", str(year)]],
        truth=f"highest RN median: {', '.join(cities)} (May {year})",
    )


# ---------------------------------------------------------------- the cases

CASES: list[Case] = [
    # dev ---------------------------------------------------------------
    Case("us_unemployment", "dev", "national",
         "What is the current U.S. unemployment rate?",
         _latest_rate("LNS14000000", "unemployment rate"), ("search_series", "get_latest")),
    Case("payrolls_change", "dev", "national",
         "How many jobs did the U.S. economy add or lose in the most recent jobs report?",
         _payrolls, ("get_latest",)),
    Case("cpi_inflation", "dev", "prices",
         "What is the current annual inflation rate as measured by the Consumer Price Index?",
         _cpi, ("get_latest", "get_series")),
    Case("rn_wages_texas", "dev", "occupations",
         "What is the median annual wage for registered nurses in Texas?",
         _oews_median("state", "48", "29-1141", "RN median, Texas"),
         ("find_occupation", "find_area", "get_occupation_wages")),
    Case("texas_unemployment", "dev", "states",
         "What is the unemployment rate in Texas right now?",
         _latest_rate("LASST480000000000003", "Texas unemployment rate"),
         ("find_area", "get_latest")),
    Case("manufacturing_real_wages", "dev", "compute",
         "Over the past year, have average hourly earnings in manufacturing kept up with "
         "inflation?",
         _real_manufacturing_wages, ("search_series", "real_wage_growth")),
    Case("initial_claims", "dev", "national",
         "How many initial unemployment insurance claims were filed in the most recent week?",
         _claims, ("get_latest",)),
    Case("travis_county_jobs", "dev", "counties",
         "How many jobs are there in Travis County, Texas, according to the latest QCEW data?",
         _travis_jobs, ("find_area", "get_area_employment")),
    Case("next_cpi_release", "dev", "calendar",
         "When is the next Consumer Price Index report scheduled to be released?",
         _next_release(10, "CPI release"), ("describe_series",)),
    Case("future_unemployment", "dev", "abstain",
         "What was the U.S. unemployment rate in December 2027?",
         _future_abstain, ()),
    Case("construction_vs_manufacturing", "dev", "compute",
         "Over the past year, which grew faster: average hourly earnings in construction or in "
         "manufacturing? Give both growth rates.",
         _two_industries, ("search_series", "compare_series")),
    Case("austin_unemployment", "dev", "metros",
         "What is the unemployment rate in the Austin, Texas metro area?",
         _latest_rate("LAUMT481242000000003", "Austin metro unemployment rate"),
         ("find_area", "get_latest")),
    Case("participation_change_year", "dev", "national",
         "How much has the U.S. labor force participation rate changed compared with a year ago?",
         _rate_change_over_year("LNS11300000", "participation rate"), ("get_latest",)),
    Case("epop_change_year", "dev", "national",
         "Compared with a year earlier, is the U.S. employment-population ratio higher or lower, "
         "and by how much?",
         _rate_change_over_year("LNS12300000", "employment-population ratio"), ("get_latest",)),
    # held_out ----------------------------------------------------------
    Case("software_devs_california", "held_out", "occupations",
         "What do software developers earn in California? I want the median annual wage.",
         _oews_median("state", "06", "15-1252", "software developer median, California"),
         ("find_occupation", "find_area", "get_occupation_wages")),
    Case("job_openings", "held_out", "national",
         "How many job openings were there in the U.S. in the latest JOLTS data?",
         _job_openings, ("search_series", "get_latest")),
    Case("private_wage_growth", "held_out", "national",
         "How fast are average hourly earnings growing for all private-sector employees, year "
         "over year?",
         _yoy("CES0500000003", "private AHE growth"), ("search_series", "get_series")),
    Case("florida_unemployment", "held_out", "states",
         "What's Florida's unemployment rate?",
         _latest_rate("LASST120000000000003", "Florida unemployment rate"),
         ("find_area", "get_latest")),
    Case("nurse_wage_travis_county", "held_out", "occupations",
         "What is the median annual wage for registered nurses in Travis County, Texas?",
         _county_nurse_wage, ("find_occupation", "find_area", "get_occupation_wages")),
    Case("next_jobs_report", "held_out", "calendar",
         "When is the next monthly jobs report (Employment Situation) coming out?",
         _next_release(50, "Employment Situation"), ("describe_series",)),
    Case("unemployment_change_year", "held_out", "national",
         "By how much has the U.S. unemployment rate changed over the past year?",
         _rate_change_over_year("LNS14000000", "unemployment rate"), ("get_latest",)),
    Case("top_metro_rn_pay", "held_out", "occupations",
         "Which U.S. metro area pays registered nurses the highest median wage?",
         _top_metro_rn, ("find_occupation", "get_occupation_wages")),
]  # fmt: skip


def select(split: str, case_ids: list[str] | None = None) -> list[Case]:
    """Cases in ``split`` (``dev``, ``held_out`` or ``all``), optionally only ``case_ids``."""
    chosen = [c for c in CASES if split == "all" or c.split == split]
    if case_ids:
        wanted = set(case_ids)
        chosen = [c for c in chosen if c.case_id in wanted]
    return chosen
