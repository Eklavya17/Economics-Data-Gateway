"""Constants shared across the gateway. Every limit states its unit and where it comes from."""

from __future__ import annotations

SERVER_NAME = "econ-gateway"

# ---------------------------------------------------------------- upstream links
BLS_SERIES_URL = "https://data.bls.gov/timeseries/{series_id}"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/{series_id}"

# ---------------------------------------------------------------- input limits
# BLS/FRED IDs are short alphanumerics (longest BLS IDs, e.g. OEWS, are ~25 chars).
SERIES_ID_PATTERN = r"^[A-Za-z0-9_.\-]{2,40}$"
# 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'.
DATE_PATTERN = r"^\d{4}(-\d{2}(-\d{2})?)?$"
SEARCH_DEFAULT_LIMIT = 10  # results
SEARCH_MAX_LIMIT = 25  # results
GET_SERIES_MAX_IDS = 10  # series per call
GET_LATEST_MAX_IDS = 20  # series per call

# ---------------------------------------------------------------- response size
# Claude Code caps a tool response at 25,000 tokens by default (docs/RESEARCH.md §2). In concise
# format a point costs ~8 tokens, so 1,200 points (~10K tokens) leaves headroom for metadata.
MAX_POINTS_DEFAULT = 60  # observations per series
MAX_POINTS_LIMIT = 500  # observations per series
TOTAL_POINTS_LIMIT = 1200  # observations across all series in one response
TRANSFORM_DECIMALS = 3  # rounding for computed changes; raw values are returned as published

# ---------------------------------------------------------------- default windows
DEFAULT_LOOKBACK_YEARS = 5  # get_series when start_date is omitted
LATEST_LOOKBACK_YEARS = 2  # get_latest: enough history for a year-ago comparison
# get_series fetches through today when end_date is this recent, to flag newer data it cut off.
RECENT_END_YEARS = 2  # years

# ---------------------------------------------------------------- search behaviour
# Add FRED full-text search when the free local catalog returns fewer hits than this.
FRED_FALLBACK_MIN_CATALOG_HITS = 3  # results
# FRED candidates fetched (one request) before the gateway re-ranks them; see services/search.py.
FRED_SEARCH_CANDIDATES = 20  # results

# ---------------------------------------------------------------- areas, occupations (Phase 2)
AREA_ID_PATTERN = r"^(us|state:\d{2}|metro:\d{5}|county:\d{5})$"
SOC_CODE_PATTERN = r"^\d{2}-\d{4}$"  # e.g. 29-1141 (Registered Nurses)
FIND_DEFAULT_LIMIT = 8  # candidates returned by find_area / find_occupation
FIND_MAX_LIMIT = 20  # candidates
WAGES_MAX_OCCUPATIONS = 5  # occupations per get_occupation_wages call
WAGES_MAX_AREAS = 25  # explicit area_ids per call
WAGES_RANK_DEFAULT_LIMIT = 15  # areas per occupation when ranking all states/metros
WAGES_RANK_MAX_LIMIT = 60  # areas per occupation
EMPLOYMENT_MAX_AREAS = 10  # areas per get_area_employment call
EMPLOYMENT_MAX_INDUSTRIES = 15  # industry keys per call
# OEWS covers May of year Y and is released the following spring, so last year's file is the
# newest that can exist; try it first, then fall back (DESIGN.md decision 14).
OEWS_YEARS_TO_TRY = 3  # years
QCEW_QUARTERS_TO_TRY = 6  # quarters stepped back from today (QCEW lags ~5-6 months)
# How long a "not published yet" answer (OEWS year, QCEW quarter) is remembered.
AVAILABILITY_CACHE_TTL_S = 12 * 3600  # seconds

OEWS_CURRENT_URL = "https://www.bls.gov/oes/current/oes{soc_digits}.htm"
OEWS_TABLES_URL = "https://www.bls.gov/oes/tables.htm"
QCEW_AREA_URL = "https://data.bls.gov/cew/data/api/{year}/{quarter}/area/{area}.csv"

# ---------------------------------------------------------------- compute (Phase 3)
COMPARE_MAX_SERIES = 25  # series per compare_series call (~25 industries / states fit easily)

# ---------------------------------------------------------------- upstream behaviour
METADATA_CACHE_TTL_S = 24 * 3600  # seconds; series titles/units change rarely
# Worst case per HTTP request: 2 attempts x (5 s connect + 30 s read) + 0.5 s backoff ~ 71 s.
TOOL_TIMEOUT_S = 90.0  # seconds
WEEKLY_MATCH_TOLERANCE_DAYS = 3  # weekly data shifts weekday year to year

# ---------------------------------------------------------------- reliability (Phase 4)
# Allow-listed upstream API hosts (CLAUDE.md rule 7). OEWS (www.bls.gov) and QCEW
# (data.bls.gov) files are downloaded by sources/oews.py and qcew.py with plain sessions (#26).
BLS_API_HOST = "api.bls.gov"
FRED_API_HOST = "api.stlouisfed.org"
FRED_API_URL = "https://api.stlouisfed.org/fred"
HTTP_CONNECT_TIMEOUT_S = 5.0  # seconds; caps the clients' 60 s default
HTTP_READ_TIMEOUT_S = 30.0  # seconds; a 50-series x 20-year BLS query takes ~2-8 s
HTTP_RETRIES = 1  # urllib3 retries on connection errors and 5xx (not 429)

# Token buckets: self-imposed, well under the documented limits. FRED allows 120 requests/min
# (fred.stlouisfed.org/docs/api/fred/); BLS's binding limit is the daily quota below.
BLS_RATE_PER_S = 2.0  # requests/second sustained
BLS_BURST = 5  # requests
FRED_RATE_PER_S = 1.5  # requests/second sustained (90/min, below FRED's 120/min)
FRED_BURST = 10  # requests
RATE_LIMIT_MAX_WAIT_S = 10.0  # seconds a request may queue for a token before failing

# Circuit breaker, per upstream API (BLS, FRED).
BREAKER_FAILURE_THRESHOLD = 3  # consecutive failed requests (after urllib3 retries)
BREAKER_RESET_TIMEOUT_S = 60.0  # seconds before a single probe request is let through

# BLS daily query quota (bls.gov/developers/): 500 with a registered key, 25 without.
BLS_DAILY_QUOTA_REGISTERED = 500  # queries/day
BLS_DAILY_QUOTA_ANONYMOUS = 25  # queries/day
# When this few queries are left, optional calls (series metadata) stop so data calls can run.
BLS_QUOTA_RESERVE_REGISTERED = 25  # queries
BLS_QUOTA_RESERVE_ANONYMOUS = 5  # queries
QUOTA_TIMEZONE = "America/New_York"  # BLS's day boundary is assumed to be US Eastern midnight
QUOTA_FILE_NAME = "bls_quota.json"  # inside Settings.state_dir

# Release-calendar cache freshness (services/release_calendar.py).
RELEASE_TIMEZONE = "America/New_York"
RELEASE_HOUR_ET = 8  # most BLS releases are at 8:30 a.m. ET (JOLTS and state data at 10:00)
RELEASE_MINUTE_ET = 30
# After a release, FRED may take a while to post it (and some releases are at 10:00), so an
# entry fetched in this window is only trusted briefly.
RELEASE_SETTLE_S = 3 * 3600  # seconds after the scheduled release time
RELEASE_SETTLE_RECHECK_S = 10 * 60  # seconds an entry fetched inside the settle window is kept
CACHE_DEFAULT_TTL_S = 12 * 3600  # seconds; series with no known release (CACHE_TTL_HOURS)
CACHE_MAX_AGE_S = 7 * 24 * 3600  # seconds; refetch at least weekly to catch unscheduled revisions
CALENDAR_LOOKBACK_DAYS = 120  # days of past release dates fetched (covers quarterly releases)
CALENDAR_LOOKAHEAD_DAYS = 60  # days of upcoming release dates fetched (for status reports)
CALENDAR_REFRESH_S = 12 * 3600  # seconds between release-date refreshes per release
CALENDAR_RETRY_S = 10 * 60  # seconds before retrying a failed release-date lookup
SERIES_RELEASE_TTL_S = 7 * 24 * 3600  # seconds; which release a FRED series belongs to

# ---------------------------------------------------------------- resources, prompts (Phase 5)
FRED_RELEASE_URL = "https://fred.stlouisfed.org/release?rid={release_id}"
# econ://catalog/{category} values look like 'unemployment', 'labor_force'.
CATEGORY_PATTERN = r"^[a-z_]{2,40}$"
METHODOLOGY_TOPIC_PATTERN = r"^[a-z0-9-]{2,40}$"  # e.g. 'seasonal-adjustment'
