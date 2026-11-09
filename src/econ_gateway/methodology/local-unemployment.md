# State, metro and county unemployment (LAUS)

Local Area Unemployment Statistics provide monthly labor force, employment, unemployment and unemployment rates for states, metro areas and counties. They are model-based estimates that combine the household survey with payroll and unemployment-claims data.

- **Series IDs follow a pattern** that `find_area` builds for you: `LASST{state FIPS}...` for states (seasonally adjusted), `LAUMT{state FIPS}{metro code}...` for metros and `LAUCN{county FIPS}...` for counties (not seasonally adjusted). The last two digits are the measure: 03 rate, 04 unemployed, 05 employed, 06 labor force.
- **Timing:** state data come out about two weeks after the national jobs report; metro and county data follow later in the month.
- **Revisions:** the latest month is preliminary and revised the next month; all estimates are re-benchmarked early the following year.
- **Not seasonally adjusted locally:** compare metro and county rates with the same month a year earlier, not with the previous month.
- **Rates change in percentage points** (see `econ://methodology/rates-and-changes`).
- **Small areas are noisy.** County rates can move by several tenths of a point on estimation error alone.
