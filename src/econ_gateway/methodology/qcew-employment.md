# County and industry employment (QCEW)

The Quarterly Census of Employment and Wages counts jobs and wages from employers' unemployment-insurance tax filings. It covers about 95% of U.S. jobs, down to the county and industry level, and is a census rather than a survey, but it is published about five to six months after each quarter ends.

- **What the gateway returns:** establishments, employment in the third month of the quarter, average weekly wage and over-the-year changes, for private ownership by sector (all ownerships for the total).
- **Latest quarter:** discovered by trying recent quarters. Expect the newest available quarter to be about two quarters behind today.
- **Suppression:** BLS withholds cells that could reveal a single employer and publishes them as zero. The gateway returns them as `disclosed: false` with null values. Never read a suppressed cell as zero jobs.
- **Seasonality:** QCEW is not seasonally adjusted. Compare a quarter with the same quarter a year earlier (the over-the-year fields), not with the previous quarter.
- **Average weekly wage** is total wages divided by employment, so it moves with the mix of jobs as well as with pay rates. It includes bonuses, which raises first-quarter wages in some industries.
- **Coverage gaps:** the self-employed, many small-farm agricultural workers and some other groups are outside unemployment-insurance coverage.
