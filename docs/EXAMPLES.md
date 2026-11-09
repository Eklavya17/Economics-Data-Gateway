# Tool examples

Abridged responses from live calls (November 2026). Full parameters and return fields for every
tool are in [TOOLS.md](TOOLS.md); whole conversations are in [DEMO.md](DEMO.md).

```jsonc
// search_series {"query": "average hourly earnings", "geography": "Texas", "limit": 3}
// The curated catalog only has the national series, so FRED is searched and re-ranked:
{"results": [
  {"series_id": "SMU48000000500000003", "title": "Average Hourly Earnings of All Employees: Total Private in Texas",
   "units": "$ per Hour", "frequency": "monthly", "seasonal_adjustment": "NSA", "geography": "Texas", "match": "fred_search"},
  ...],
 "fred_searched": true, "notes": ["Also searched FRED because no catalog series cover Texas."]}

// get_latest {"series_ids": ["LNS14000000"]}
{"results": [{"meta": {"series_id": "LNS14000000", "title": "Unemployment rate", "units": "percent",
               "url": "https://data.bls.gov/timeseries/LNS14000000", ...},
              "latest_period": "2026-10", "latest_value": 4.1, "previous_period": "2026-09", "previous_value": 4.2,
              "change_from_previous": -0.1, "pct_change_from_previous": null, "change_units": "percentage points",
              "year_ago_period": "2025-10", "year_ago_value": null,
              "notes": ["The source has no published value for 2025-10, so the change from a year earlier cannot
                         be computed and the year_ago fields are null. The nearest published periods are
                         2025-09 (4.4) and 2025-11 (4.5). Tell the user that 2025-10 was not published; ..."]}]}

// get_series {"series_ids": ["CUSR0000SA0"], "transform": "pct_change_year_ago", "max_points": 6}
{"results": [{"value_units": "percent change from year ago",
              "points": [["2026-04", 3.779], ["2026-05", 4.167], ..., ["2026-09", 3.717]],
              "latest_available": "2026-09", "returned": 6, "available": 69, "truncated": true, ...}],
 "notes": ["Some series were truncated to their most recent observations ..."]}

// get_occupation_wages {"occupation_codes": ["29-1141"], "area_ids": ["state:48"]}
{"reference_period": "May 2025",
 "rows": [{"area_id": "state:48", "occupation": "Registered Nurses", "employment": 271380,
           "annual_median": 95970, "annual_mean": 95380, "hourly_median": 46.14, "top_coded": [], ...}],
 "national": [{"area_id": "us", "annual_median": 97550, ...}],
 "sources": ["https://www.bls.gov/oes/current/oes291141.htm"]}

// find_area {"query": "Austin"} → metro:12420 first; its laus.unemployment_rate is
// LAUMT481242000000003, which get_latest returns as 3.7% (Sep 2026, NSA, preliminary).

// get_area_employment {"area_ids": ["county:48453"]}
{"reference_period": "2026-Q1",
 "rows": [{"area_name": "Travis County, Texas", "industry": "total", "employment": 931218,
           "avg_weekly_wage": 2087.0, "avg_weekly_wage_pct_change_year_ago": 5.4, "disclosed": true, ...}]}

// real_wage_growth {"wage_series_id": "CES3000000003"}   (manufacturing)
{"deflator": {"series_id": "CUSR0000SA0", ...}, "base_period": "2026-09",
 "latest": {"period": "2026-09", "nominal_wage": 37.06, "nominal_pct_change_year_ago": 3.81,
            "inflation_pct_change_year_ago": 3.717, "real_pct_change_year_ago": 0.089},
 "real_wage_rising": true,
 "notes": ["Wages are published through 2026-10 but prices are not yet; results end at 2026-09."]}

// compare_series {"series_ids": [10 industry earnings IDs], "measure": "pct_change_year_ago",
//                 "benchmark_series_id": "CUSR0000SA0"}
{"rows": [{"rank": 1, "meta": {"title": "Average hourly earnings ..., Information", ...},
           "value": 5.466, "gap_vs_benchmark": 1.749, "latest_period": "2026-09"}, ...],
 "benchmark": {"value": 3.717, "latest_period": "2026-09", ...},
 "notes": ["Compared at 2026-09, the benchmark's latest period, so every gap uses the same months. ..."]}
```
