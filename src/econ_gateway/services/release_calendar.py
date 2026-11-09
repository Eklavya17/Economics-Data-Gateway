"""When is a cached observation out of date? When its release has come out since it was fetched.

BLS and FRED data change on a published schedule (the Employment Situation on the first
Friday, CPI mid-month, jobless claims every Thursday...). FRED publishes that schedule for
every release it carries, BLS releases included, through ``fred/release/dates``
(https://fred.stlouisfed.org/docs/api/fred/release_dates.html). ``ReleaseCalendar.is_fresh``
uses it as follows:

1. Find the series' release: a FRED lookup (``fred/series/release``) for FRED IDs, or the
   ID-prefix table below for BLS IDs, which FRED does not know by their BLS names.
2. Find the latest scheduled release instant at or before now (date + 8:30 a.m. ET).
3. Fresh if the entry was fetched after that instant plus a settle window. An entry fetched
   *during* the window is kept only briefly, because FRED may still be posting the release
   (and a few BLS releases come out at 10:00, not 8:30).
4. Unknown release or no calendar (no FRED key, FRED down) → the fixed 12 h TTL.
   Every entry is refetched after ``CACHE_MAX_AGE_S`` regardless, to catch unscheduled
   revisions.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from econ_gateway.consts import (
    CACHE_DEFAULT_TTL_S,
    CACHE_MAX_AGE_S,
    CALENDAR_LOOKAHEAD_DAYS,
    CALENDAR_LOOKBACK_DAYS,
    CALENDAR_REFRESH_S,
    CALENDAR_RETRY_S,
    FRED_API_URL,
    HTTP_READ_TIMEOUT_S,
    RELEASE_HOUR_ET,
    RELEASE_MINUTE_ET,
    RELEASE_SETTLE_RECHECK_S,
    RELEASE_SETTLE_S,
    RELEASE_TIMEZONE,
    SERIES_RELEASE_TTL_S,
)

log = logging.getLogger(__name__)

RELEASE_TZ = ZoneInfo(RELEASE_TIMEZONE)

# BLS series-ID prefix → FRED release ID. Longest prefixes first (LASST before LA...).
# Release IDs were checked against fred/series/release for the FRED copies of these series
# (e.g. UNRATE → 50, CPIAUCSL → 10, TXUR → 112, AUST448URN → 113).
BLS_RELEASE_BY_PREFIX: tuple[tuple[str, int], ...] = (
    ("LASST", 112),  # LAUS states: State Employment and Unemployment
    ("LAUMT", 113),  # LAUS metros: Metropolitan Area Employment and Unemployment
    ("LAUCN", 116),  # LAUS counties: Unemployment in States and Local Areas (all other areas)
    ("SM", 112),  # state/metro CES: State Employment and Unemployment
    ("CE", 50),  # national CES: Employment Situation
    ("LN", 50),  # CPS: Employment Situation
    ("CU", 10),  # CPI-U: Consumer Price Index
    ("CW", 10),  # CPI-W
    ("AP", 10),  # CPI average prices
    ("JT", 192),  # Job Openings and Labor Turnover Survey
    ("CI", 11),  # Employment Cost Index
    ("PR", 47),  # Productivity and Costs
    ("WP", 46),  # Producer Price Index (commodities)
    ("PC", 46),  # Producer Price Index (industries)
)

_NO_RELEASE = -1  # cached answer for "FRED knows no release for this series"


def bls_release_id(series_id: str) -> int | None:
    """FRED release ID for a BLS series, from its ID prefix; ``None`` when unmapped."""
    for prefix, release_id in BLS_RELEASE_BY_PREFIX:
        if series_id.startswith(prefix):
            return release_id
    return None


def release_instant(day: date) -> float:
    """Epoch seconds of the scheduled release time (8:30 a.m. US Eastern) on ``day``."""
    moment = datetime(
        day.year, day.month, day.day, RELEASE_HOUR_ET, RELEASE_MINUTE_ET, tzinfo=RELEASE_TZ
    )
    return moment.timestamp()


# ---------------------------------------------------------------- FRED release endpoints


def _fred_get(
    session: requests.Session, api_key: str, endpoint: str, params: dict[str, str]
) -> requests.Response:
    """GET a FRED endpoint. Errors never include the URL, which carries the API key."""
    return session.get(
        f"{FRED_API_URL}/{endpoint}",
        params={**params, "api_key": api_key, "file_type": "json"},
        timeout=HTTP_READ_TIMEOUT_S,
    )


def fetch_release_dates(
    session: requests.Session, api_key: str, release_id: int, start: date, end: date
) -> list[date]:
    """Scheduled dates of one release between ``start`` and ``end``, ascending.

    Raises:
        requests.RequestException: Network failure or a non-200 answer.
    """
    response = _fred_get(
        session,
        api_key,
        "release/dates",
        {
            "release_id": str(release_id),
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            # Without this, FRED omits scheduled dates whose data is not out yet.
            "include_release_dates_with_no_data": "true",
            "sort_order": "asc",
            "limit": "1000",
        },
    )
    if response.status_code != 200:
        raise requests.HTTPError(f"FRED release/dates returned HTTP {response.status_code}")
    return [date.fromisoformat(item["date"]) for item in response.json()["release_dates"]]


def fetch_series_release(session: requests.Session, api_key: str, series_id: str) -> int | None:
    """FRED release ID of a FRED series; ``None`` when FRED does not know the series.

    Raises:
        requests.RequestException: Network failure or an unexpected non-200 answer.
    """
    response = _fred_get(session, api_key, "series/release", {"series_id": series_id})
    if response.status_code == 400:  # unknown or malformed series ID
        return None
    if response.status_code != 200:
        raise requests.HTTPError(f"FRED series/release returned HTTP {response.status_code}")
    releases = response.json().get("releases", [])
    return int(releases[0]["id"]) if releases else None


# ---------------------------------------------------------------- the calendar

ReleaseDatesLoader = Callable[[int, date, date], list[date]]
SeriesReleaseLoader = Callable[[str], int | None]


class ReleaseCalendar:
    """Release schedule lookups and the freshness rule for cached observations.

    Lookups are cached in memory; concurrent misses may fetch twice, which is harmless.

    Args:
        load_release_dates: ``(release_id, start, end) -> dates``; ``None`` when FRED is not
            configured, in which case every entry gets the fixed TTL.
        load_series_release: ``series_id -> release_id`` for FRED series; ``None`` likewise.
        clock: Wall clock in epoch seconds; injectable for tests.
    """

    def __init__(
        self,
        load_release_dates: ReleaseDatesLoader | None,
        load_series_release: SeriesReleaseLoader | None,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._load_release_dates = load_release_dates
        self._load_series_release = load_series_release
        self._clock = clock
        self._dates: dict[int, tuple[float, list[date]]] = {}  # release → (expires_at, dates)
        self._series: dict[str, tuple[float, int]] = {}  # FRED series → (expires_at, release)

    # ------------------------------------------------------------ lookups

    def release_id(self, namespace: str, series_id: str) -> int | None:
        """Release that publishes ``series_id`` (``namespace`` is the cache's ``bls``/``fred``)."""
        if namespace == "bls":
            return bls_release_id(series_id)
        if namespace != "fred" or self._load_series_release is None:
            return None
        now = self._clock()
        hit = self._series.get(series_id)
        if hit is None or hit[0] <= now:
            try:
                found = self._load_series_release(series_id)
                hit = (now + SERIES_RELEASE_TTL_S, _NO_RELEASE if found is None else found)
            except (requests.RequestException, ValueError, KeyError) as exc:
                log.info("series release lookup failed", extra={"err": type(exc).__name__})
                hit = (now + CALENDAR_RETRY_S, _NO_RELEASE)
            self._series[series_id] = hit
        return None if hit[1] == _NO_RELEASE else hit[1]

    def release_dates(self, release_id: int) -> list[date]:
        """Scheduled dates from ``CALENDAR_LOOKBACK_DAYS`` ago to ``CALENDAR_LOOKAHEAD_DAYS`` ahead.

        Returns an empty list when the schedule is unavailable (retried after a few minutes).
        """
        if self._load_release_dates is None:
            return []
        now = self._clock()
        hit = self._dates.get(release_id)
        if hit is not None and hit[0] > now:
            return hit[1]
        today = datetime.fromtimestamp(now, RELEASE_TZ).date()
        try:
            dates = self._load_release_dates(
                release_id,
                today - timedelta(days=CALENDAR_LOOKBACK_DAYS),
                today + timedelta(days=CALENDAR_LOOKAHEAD_DAYS),
            )
            hit = (now + CALENDAR_REFRESH_S, sorted(dates))
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.info("release calendar lookup failed", extra={"err": type(exc).__name__})
            # Keep the last known schedule if there is one; retry soon either way.
            hit = (now + CALENDAR_RETRY_S, hit[1] if hit else [])
        self._dates[release_id] = hit
        return hit[1]

    def last_release_at(self, release_id: int, now: float) -> float | None:
        """Epoch seconds of the latest scheduled release instant at or before ``now``."""
        past = [t for t in map(release_instant, self.release_dates(release_id)) if t <= now]
        return past[-1] if past else None

    def next_release(self, release_id: int) -> date | None:
        """Next scheduled release date after now (for status reports)."""
        now = self._clock()
        upcoming = [d for d in self.release_dates(release_id) if release_instant(d) > now]
        return upcoming[0] if upcoming else None

    # ------------------------------------------------------------ the rule

    def is_fresh(self, namespace: str, series_id: str, fetched_at: float) -> bool:
        """Whether a cache entry fetched at ``fetched_at`` still matches the published data.

        Args:
            namespace: The disk cache namespace (``bls`` or ``fred``).
            series_id: The cached series.
            fetched_at: Epoch seconds when the entry was stored.
        """
        now = self._clock()
        age_s = now - fetched_at
        if age_s >= CACHE_MAX_AGE_S:
            return False
        release_id = self.release_id(namespace, series_id)
        last = self.last_release_at(release_id, now) if release_id is not None else None
        if last is None:
            return age_s < CACHE_DEFAULT_TTL_S
        if fetched_at < last:
            return False  # a release came out after this copy was fetched
        if fetched_at < last + RELEASE_SETTLE_S:
            return age_s < RELEASE_SETTLE_RECHECK_S  # fetched while the release was landing
        return True
