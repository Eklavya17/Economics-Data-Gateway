# Rates, changes and comparisons

How the gateway reports changes, and how to describe them correctly. Most mistakes come from treating a change in a rate as a percent change, or from comparing different periods.

- **Rates change in percentage points.** If unemployment goes from 4.1% to 4.3%, it rose 0.2 percentage points, not 4.9%. For series measured in percent, the gateway reports `change_*` fields in percentage points and leaves `pct_change_*` empty, and `compare_series` refuses percent changes of rates.
- **Year-over-year** compares a period with the same period a year earlier; it is the safest comparison for unadjusted data. The gateway matches by date, and matches weekly data (e.g. jobless claims, weeks ending Saturday) to the nearest week within 3 days.
- **Missing periods are reported, not filled.** If the source has no value for the comparison period, the change is null and a note explains why (e.g. BLS published no October 2025 household-survey or CPI data because of the federal government shutdown).
- **"Since YEAR"** in `compare_series` means that year's average; "since YYYY-MM" means that period's value.
- **Benchmarks** in `compare_series` are compared at the same period: every series is measured at the benchmark's latest period.
- **Rounding:** computed changes are rounded to 3 decimals; raw values are returned as published.
- **Always quote the period** each number refers to. It is usually one to two months before today.
