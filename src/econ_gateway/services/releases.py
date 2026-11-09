"""Release schedules: which release publishes a series, and when it last and next comes out.

Used by ``describe_series`` (so a model can tell whether newer data is due) and by the
``econ://releases`` resource. Dates come from FRED's release calendar, which covers BLS
releases too; see ``services/release_calendar.py`` for how they are fetched and cached.
"""

from __future__ import annotations

from datetime import date, datetime

from econ_gateway.consts import FRED_RELEASE_URL
from econ_gateway.models import ReleaseCalendarResponse, ReleaseInfo, Source
from econ_gateway.services.release_calendar import RELEASE_TZ
from econ_gateway.services.upstream import Upstream

# Names of the releases behind the gateway's data, keyed by FRED release ID. Order is the
# order of ``econ://releases``: headline labor-market and price releases first.
KEY_RELEASES: dict[int, str] = {
    50: "Employment Situation",
    10: "Consumer Price Index",
    180: "Unemployment Insurance Weekly Claims Report",
    192: "Job Openings and Labor Turnover Survey",
    11: "Employment Cost Index",
    112: "State Employment and Unemployment",
    113: "Metropolitan Area Employment and Unemployment",
    116: "Unemployment in States and Local Areas (all other areas)",
    46: "Producer Price Index",
    47: "Productivity and Costs",
    54: "Personal Income and Outlays",
    53: "Gross Domestic Product",
    18: "H.15 Selected Interest Rates",
}
# Daily releases (interest rates) would crowd the calendar without telling anyone much.
_CALENDAR_EXCLUDED = {18}


def today_eastern() -> date:
    """Today's date in US Eastern time, the time zone of the release schedule."""
    return datetime.now(RELEASE_TZ).date()


def release_info(release_id: int, upstream: Upstream, today: date) -> ReleaseInfo:
    """Last and next dates of one release.

    Args:
        release_id: FRED release ID.
        upstream: Data access (its calendar is cached).
        today: Reference date in US Eastern time.

    Returns:
        The release with whatever dates are known (both ``None`` if the schedule is unavailable).
    """
    dates = upstream.release_dates(release_id)
    past = [d for d in dates if d <= today]
    upcoming = [d for d in dates if d > today]
    return ReleaseInfo(
        release_id=release_id,
        name=KEY_RELEASES.get(release_id),
        last_release_date=past[-1].isoformat() if past else None,
        next_release_date=upcoming[0].isoformat() if upcoming else None,
        url=FRED_RELEASE_URL.format(release_id=release_id),
    )


def series_release(
    series_id: str, source: Source, upstream: Upstream, today: date
) -> ReleaseInfo | None:
    """The release that publishes ``series_id``; ``None`` when it cannot be determined."""
    release_id = upstream.release_id(series_id, source)
    return None if release_id is None else release_info(release_id, upstream, today)


def release_calendar(upstream: Upstream, *, today: date | None = None) -> ReleaseCalendarResponse:
    """Schedule of the headline releases, soonest next release first.

    Args:
        upstream: Data access.
        today: Override for tests; defaults to today in US Eastern time.

    Returns:
        The calendar body for ``econ://releases``.
    """
    today = today or today_eastern()
    releases = [
        release_info(release_id, upstream, today)
        for release_id in KEY_RELEASES
        if release_id not in _CALENDAR_EXCLUDED
    ]
    releases.sort(key=lambda r: r.next_release_date or "9999-12-31")
    notes = ["Most BLS releases come out at 8:30 a.m. ET; JOLTS and state data at 10:00 a.m."]
    if not upstream.has_fred_key:
        notes.append(
            "FRED_API_KEY is not set on the gateway server, so release dates are unavailable."
        )
    elif all(r.next_release_date is None for r in releases):
        notes.append("FRED's release calendar could not be reached; try again shortly.")
    return ReleaseCalendarResponse(as_of=today.isoformat(), releases=releases, notes=notes)
