# Inflation measures and real wages

A real (inflation-adjusted) wage change compares pay growth with price growth over the same period. The gateway computes it exactly, as (1 + wage growth) / (1 + inflation) − 1, never as a simple difference.

- **CPI-U** (`CUSR0000SA0` SA, `CUUR0000SA0` NSA) covers all urban consumers, over 90% of the population, and is the usual deflator.
- **CPI-W** (`CWSR0000SA0`, `CWUR0000SA0`) covers urban wage earners and clerical workers and is used for Social Security cost-of-living adjustments.
- **PCE price index** (`PCEPI`, from BEA via FRED) has broader scope and weights that follow spending; the Federal Reserve's 2% inflation target is defined on it. It usually runs below CPI.
- **Matching:** `real_wage_growth` uses the price index with the same seasonal adjustment as the wage series. When frequencies differ, it averages prices over complete periods (e.g. monthly CPI to quarterly for the Employment Cost Index).
- **Release timing:** the jobs report usually comes out about a week before CPI, so the newest wage month often has no price data yet. Results stop at the last period with both, and the response says so.
- **Real levels** are expressed in dollars of the latest common period.
- **Average hourly earnings vs the ECI:** average hourly earnings shift when the mix of jobs changes (losing many low-wage jobs raises the average). The Employment Cost Index holds the job mix fixed and is the cleaner measure of pay growth, but it is quarterly.
