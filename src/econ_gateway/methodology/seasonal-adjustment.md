# Seasonal adjustment

Seasonally adjusted (SA) series remove regular calendar patterns (holiday hiring, summer jobs, school years) so that month-to-month moves can be compared. Not seasonally adjusted (NSA) series are the raw estimates.

- **Month-to-month changes:** use SA series. An NSA December jump in retail jobs is mostly seasonal.
- **Year-over-year changes:** SA and NSA give similar answers because the same month is compared. Use NSA when only NSA exists.
- **IDs often encode it.** In many BLS IDs the third character is `S` or `U`: `CUSR0000SA0` (CPI, SA) vs `CUUR0000SA0` (NSA), `LNS14000000` vs `LNU04000000`. Every gateway response states it in `meta.seasonal_adjustment`.
- **Local data is mostly NSA.** The gateway's metro and county unemployment series (`LAUMT...`, `LAUCN...`) and all OEWS and QCEW figures are not seasonally adjusted. State unemployment rates (`LASST...`) are SA.
- **Do not mix** SA and NSA series in one comparison. `real_wage_growth` picks the price index with the same adjustment as the wage series for this reason.
- **SA history changes.** Seasonal factors are re-estimated every year, so SA values for past years are revised (see `econ://methodology/revisions`).
