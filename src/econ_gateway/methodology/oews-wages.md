# Occupational wages (OEWS)

Occupational Employment and Wage Statistics give employment and wage percentiles for about 800 occupations, nationally and by state and metro area. They describe pay levels in May of one year; they are not a monthly or quarterly time series.

- **Reference period:** each release describes May of year Y and is published the following spring. The gateway reports the year as `May YYYY` and finds the newest published year by trying, never by assuming.
- **Sample design:** each estimate pools survey responses collected over three years, so year-to-year differences mix real change with sample turnover. For pay trends, use the time-series tools (`get_series`, `real_wage_growth`).
- **Measures:** hourly and annual mean and median, plus 10th, 25th, 75th and 90th percentiles. Annual wages are generally hourly wages times 2,080 hours.
- **Top-coding:** very high wages are published only as "at or above" a cap. The gateway lists those fields in `top_coded` and returns no number for them; say "at least the cap" rather than quoting a value.
- **Annual-only occupations** (e.g. teachers) have no hourly wage; the gateway sets `annual_only`.
- **Codes:** occupations use SOC codes such as `29-1141` (Registered Nurses). Get them from `find_occupation`; areas come from `find_area`.
- **Geography:** national, state and metro estimates are available through the gateway. OEWS does not publish county estimates.
