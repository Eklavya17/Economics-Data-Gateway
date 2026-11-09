# Data freshness and outages

Data is current as of the latest agency release, which always lags today's date. The gateway keeps cached data until its release next comes out, and never invents values.

- **Release schedule:** `describe_series` returns `release.last_release_date` and `release.next_release_date`. No newer period will exist before the next release date. `econ://releases` lists the headline releases. Most BLS releases are at 8:30 a.m. ET.
- **Caching:** an observation stays cached until its release publishes again, and is refetched at least weekly. For a few hours after a release, cached data is rechecked every few minutes while FRED posts the new figures.
- **Outages:** if BLS or FRED cannot be reached, the gateway returns its last cached copy, sets `meta.stale_as_of` to the time that copy was fetched, and adds a note. When quoting such numbers, say a newer release may exist.
- **Limits:** the gateway paces its requests and tracks the BLS daily quota (500 queries per day with a key). If the quota runs out, FRED often carries the same BLS data under a different ID (e.g. `UNRATE` for `LNS14000000`); find it with `search_series`.
- **Gaps:** some periods were never published (e.g. October 2025 household-survey and CPI data, because of the federal government shutdown). They come back as null, not as estimates.
