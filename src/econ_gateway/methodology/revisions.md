# Preliminary values and revisions

Recent values are often estimates that the agency revises as more data arrives. The gateway always returns the currently published value and flags preliminary ones; it never tracks or predicts revisions.

- **Preliminary flag.** BLS marks preliminary observations with footnote code P. The gateway reports them in `preliminary_periods` (get_series) and `preliminary` (get_latest). Say "preliminary" when quoting them.
- **Payroll jobs and earnings (CES, `CE...`):** each month's first estimate is revised in the next two monthly releases. Once a year, with January data published in early February, the series is benchmarked to unemployment-insurance tax records, which can move levels by hundreds of thousands of jobs.
- **Household survey (CPS, `LN...`):** monthly values are not revised, but seasonal factors for the last five years are updated each January. New population controls each January can shift levels without any real change in the labor market.
- **CPI (`CU...`, `CW...`):** unadjusted indexes are final when published. Seasonally adjusted values for the previous five years are recalculated each February.
- **Local unemployment (LAUS, `LA...`):** the latest month is revised the following month, and each year's data is re-benchmarked early the next year.
- **QCEW:** a quarter's figures can be revised in later quarterly releases.
- **Consequence:** the same question asked a month apart can get a different answer for the same period. Quote the period, and say the figure is as currently published.
