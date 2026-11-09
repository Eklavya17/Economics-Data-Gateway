# Demo: questions answered through the gateway

Recorded November 08, 2026 by `scripts/demo.py`. The model is `openai/gpt-4.1-mini`, driven by the eval reference client: it connects to the gateway over stdio, as Claude Desktop does, and may only use the gateway's tools. Every number below came from live BLS and FRED data at recording time. Tool results are shortened; the model saw them in full.

Re-record: `.venv/Scripts/python.exe scripts/demo.py`

## What did wages look like for registered nurses in Texas last year?

**1. `find_occupation`** `{"query": "registered nurse", "limit": 1}`

```json
{"query":"registered nurse","results":[{"occupation_code":"29-1141","title":"Registered Nurses","group":"detailed","score":6.48}]}
```

**2. `find_area`** `{"query": "Texas", "level": "state", "limit": 1}`

```json
{"query":"Texas","results":[{"area_id":"state:48","name":"Texas","level":"state","has_occupation_wages":true,"has_area_employment":true,"laus":{"unemployment_rate":"LASST480000000000003","unemployed":"LASST480000000000004","employed":"LASST480000000000005","labor_force":"LASST480000000000006","seasonal_adjustment":"SA"},"score":8.5}],"notes":[]}
```

**3. `get_occupation_wages`** `{"occupation_codes":["29-1141"],"area_ids":["state:48"],"year":2025}`

```json
{"reference_period":"May 2025","rows":[{"area_id":"state:48","area_name":"Texas","occupation_code":"29-1141","occupation":"Registered Nurses","employment":271380.0,"jobs_per_1000":19.287,"location_quotient":0.89,"hourly_mean":45.86,"hourly_median":46.14,"annual_mean":95380.0,"annual_median":95970.0,"annual_p10":67120.0,"annual_p25":null,"annual_p75":null,"annual_p90":127950.0,"annual_only":false,"top_coded":[]}],"national":[{"area_id":"us","area_name":"United States","occupation_code":"29-1141","occupation":"Registered Nurses","employment":3379720.0,"jobs_per_1000":null,"location_quotient":null,"hourly_mean":48.76,"hourly_median":46.9,"annual_mean":101420.0,"annual_median":97550.0,"annual_p1 ... (1,194 characters in full)
```

**Answer**

> For registered nurses in Texas in May 2025, the annual median wage was $95,970, with an annual mean wage of $95,380. The hourly median wage was $46.14, and the hourly mean wage was $45.86. These figures are from the Occupational Employment and Wage Statistics (OEWS) survey published by the BLS in spring 2026. 
>
> Source: https://www.bls.gov/oes/current/oes291141.htm

_3 tool calls, 0 errors, 15,229 tokens, 9.21 s._

## Have manufacturing workers' hourly earnings kept up with inflation over the past year?

**1. `search_series`** `{"query":"average hourly earnings manufacturing"}`

```json
{"query":"average hourly earnings manufacturing","results":[{"series_id":"CES3000000003","source":"BLS","title":"Average hourly earnings of all employees, Manufacturing","units":"dollars per hour","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","latest_observation":"2026-09","match":"catalog"},{"series_id":"CES3000000008","source":"BLS","title":"Average hourly earnings of production and nonsupervisory employees, Manufacturing","units":"dollars per hour","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","latest_observation":"2026-09","match":"catalog"},{"series_id":"CES3200000003","source":"BLS","title":"Average hourly earnings of all em ... (2,798 characters in full)
```

**2. `real_wage_growth`** `{"wage_series_id":"CES3000000003","deflator":"cpi_u","start_date":"2025-09"}`

```json
{"wage":{"series_id":"CES3000000003","source":"BLS","title":"Average hourly earnings of all employees, Manufacturing","units":"dollars per hour","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","url":"https://data.bls.gov/timeseries/CES3000000003","notes":null,"stale_as_of":null},"deflator":{"series_id":"CUSR0000SA0","source":"BLS","title":"CPI-U, All items, U.S. city average","units":"index 1982-84=100","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","url":"https://data.bls.gov/timeseries/CUSR0000SA0","notes":null,"stale_as_of":null},"frequency":"monthly","base_period":"2026-09","latest":{"period":"2026-09","nominal_wage":37.06,"nomin ... (3,112 characters in full)
```

**Answer**

> Over the past year ending in September 2026, the average hourly earnings of manufacturing workers increased by 3.81% nominally, while inflation (CPI-U) rose by 3.717%. This results in a slight real wage increase of about 0.09%, meaning manufacturing workers' hourly earnings have just about kept up with inflation over the past year. 
>
> (Source: BLS, Average hourly earnings of all employees, Manufacturing, series CES3000000003, and CPI-U All items, series CUSR0000SA0, data as of September 2026)

_2 tool calls, 0 errors, 16,794 tokens, 5.96 s._

## How is the job market in Travis County, Texas? Give jobs, pay and unemployment.

**1. `get_area_employment`** `{"area_ids": ["county:48453"], "industries": ["total"]}`

```json
{"reference_period":"2026-Q1","rows":[{"area_id":"county:48453","area_name":"Travis County, Texas","industry":"total","industry_title":"All industries, all ownerships","ownership":"total","disclosed":true,"establishments":50881,"employment":931218,"avg_weekly_wage":2087.0,"employment_change_year_ago":594,"employment_pct_change_year_ago":0.1,"avg_weekly_wage_pct_change_year_ago":5.4}],"failed":[],"notes":["QCEW 2026-Q1: jobs covered by unemployment insurance (about 95% of U.S. jobs; excludes most self-employed). employment is the third month of the quarter.","QCEW is not seasonally adjusted: compare with the same quarter a year earlier (the *_year_ago fields), not with the previous quarter."] ... (771 characters in full)
```

**2. `find_area`** `{"query": "Travis County, Texas", "level": "county"}`

```json
{"query":"Travis County, Texas","results":[{"area_id":"county:48453","name":"Travis County, Texas","level":"county","has_occupation_wages":false,"has_area_employment":true,"laus":{"unemployment_rate":"LAUCN484530000000003","unemployed":"LAUCN484530000000004","employed":"LAUCN484530000000005","labor_force":"LAUCN484530000000006","seasonal_adjustment":"NSA"},"score":3.5},{"area_id":"county:48379","name":"Rains County, Texas","level":"county","has_occupation_wages":false,"has_area_employment":true,"laus":{"unemployment_rate":"LAUCN483790000000003","unemployed":"LAUCN483790000000004","employed":"LAUCN483790000000005","labor_force":"LAUCN483790000000006","seasonal_adjustment":"NSA"},"score":1.13} ... (2,691 characters in full)
```

**3. `find_area`** `{"query": "Travis County, Texas", "level": "county"}`

```json
{"query":"Travis County, Texas","results":[{"area_id":"county:48453","name":"Travis County, Texas","level":"county","has_occupation_wages":false,"has_area_employment":true,"laus":{"unemployment_rate":"LAUCN484530000000003","unemployed":"LAUCN484530000000004","employed":"LAUCN484530000000005","labor_force":"LAUCN484530000000006","seasonal_adjustment":"NSA"},"score":3.5},{"area_id":"county:48379","name":"Rains County, Texas","level":"county","has_occupation_wages":false,"has_area_employment":true,"laus":{"unemployment_rate":"LAUCN483790000000003","unemployed":"LAUCN483790000000004","employed":"LAUCN483790000000005","labor_force":"LAUCN483790000000006","seasonal_adjustment":"NSA"},"score":1.13} ... (2,691 characters in full)
```

**4. `get_latest`** `{"series_ids":["LAUCN484530000000003"]}`

```json
{"results":[{"meta":{"series_id":"LAUCN484530000000003","source":"BLS","title":"Unemployment Rate: Travis County, TX (U)","units":"percent","frequency":"monthly","seasonal_adjustment":"NSA","geography":"Travis County, TX","url":"https://data.bls.gov/timeseries/LAUCN484530000000003","notes":null,"stale_as_of":null},"latest_period":"2026-09","latest_value":3.6,"preliminary":true,"previous_period":"2026-08","previous_value":3.9,"change_from_previous":-0.3,"pct_change_from_previous":null,"year_ago_period":"2025-09","year_ago_value":3.6,"change_from_year_ago":0.0,"pct_change_from_year_ago":null,"change_units":"percentage points","notes":[]}],"failed":[],"retrieved_at":"2026-11-09T03:15:52+00:00"}
```

**Answer**

> The job market in Travis County, Texas as of the first quarter of 2026 shows the following:
>
> - Employment: There are approximately 931,218 jobs covered by unemployment insurance in Travis County.
> - Average Weekly Wage: The average weekly wage is about $2,087, which increased by 5.4% compared to the same quarter a year ago.
> - Employment Change: Employment increased slightly by 594 jobs (0.1%) compared to the first quarter of 2025.
> - Unemployment Rate: The unemployment rate in Travis County was 3.6% in September 2026, which is the same as it was in September 2025 but down from 3.9% in August 2026.
>
> Sources:
> - Employment and wages data: QCEW 2026-Q1 (https://data.bls.gov/cew/data/api/2026/1/area/48453.csv)
> - Unemployment rate: BLS Local Area Unemployment Statistics for Travis County, TX (https://data.bls.gov/timeseries/LAUCN484530000000003)
>
> Let me know if you want more details or data for specific industries or time periods.

_4 tool calls, 0 errors, 19,402 tokens, 8.62 s._

## How has the U.S. unemployment rate changed over the past year?

**1. `search_series`** `{"query":"unemployment rate","geography":"United States","limit":1}`

```json
{"query":"unemployment rate","results":[{"series_id":"UNRATE","source":"FRED","title":"Unemployment rate","units":"percent","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","latest_observation":"2026-09","match":"catalog"}],"fred_searched":true,"notes":["Also searched FRED because the catalog had only 1 match(es)."]}
```

**2. `get_latest`** `{"series_ids":["UNRATE"]}`

```json
{"results":[{"meta":{"series_id":"UNRATE","source":"FRED","title":"Unemployment rate","units":"percent","frequency":"monthly","seasonal_adjustment":"SA","geography":"United States","url":"https://fred.stlouisfed.org/series/UNRATE","notes":null,"stale_as_of":null},"latest_period":"2026-10","latest_value":4.1,"preliminary":false,"previous_period":"2026-09","previous_value":4.2,"change_from_previous":-0.1,"pct_change_from_previous":null,"year_ago_period":"2025-10","year_ago_value":null,"change_from_year_ago":null,"pct_change_from_year_ago":null,"change_units":"percentage points","notes":["The source has no published value for 2025-10, so the change from a year earlier cannot be computed and the ... (1,000 characters in full)
```

**Answer**

> The U.S. unemployment rate was 4.1% in October 2026, down from 4.2% in September 2026. However, the official data for October 2025 is not published, so the exact change over the past year cannot be computed. The closest published values are 4.4% in September 2025 and 4.5% in November 2025. Source: FRED (https://fred.stlouisfed.org/series/UNRATE).

_2 tool calls, 0 errors, 14,857 tokens, 5.07 s._
