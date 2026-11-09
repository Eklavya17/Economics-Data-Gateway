"""BLS Public Data API v2 client.

Docs: https://www.bls.gov/developers/api_signature_v2.htm

Limits per query:
    registered key   -> 50 series, 20 years, 500 queries/day
    no key           -> 25 series, 10 years,  25 queries/day

The client batches series and year windows automatically and caches each series separately.

Origin: Project 1 (``labor_agent.data.bls``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

import pandas as pd
import requests

from econ_gateway.sources.cache import DiskCache, default_cache
from econ_gateway.sources.http import DEFAULT_TIMEOUT, make_session
from econ_gateway.sources.settings import get_source_settings

log = logging.getLogger(__name__)

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
LIMITS = {True: (50, 20), False: (25, 10)}  # registered? -> (max series, max years) per query

COLUMNS = [
    "series_id", "date", "year", "period", "period_name", "freq", "value", "footnotes", "source",
]  # fmt: skip


class BLSError(RuntimeError):
    """BLS answered, but refused or failed the request (status other than REQUEST_SUCCEEDED)."""


def parse_period(year: int, period: str) -> tuple[datetime | None, str]:
    """Map a BLS ``(year, period)`` pair to ``(period-start date, frequency code)``.

    M01-M12 -> monthly ``M``; M13 -> annual average ``A``.
    Q01-Q04 -> quarterly ``Q``; Q05 -> annual average ``A``.
    S01-S02 -> semiannual ``S`` (Jan / Jul); S03 -> annual average ``A``.
    A01     -> annual ``A``.
    Unknown periods return ``(None, "?")``.
    """
    kind, num = period[:1], int(period[1:])
    if kind == "M":
        if 1 <= num <= 12:
            return datetime(year, num, 1), "M"
        if num == 13:
            return datetime(year, 1, 1), "A"
    elif kind == "Q":
        if 1 <= num <= 4:
            return datetime(year, 3 * (num - 1) + 1, 1), "Q"
        if num == 5:
            return datetime(year, 1, 1), "A"
    elif kind == "S":
        if num in (1, 2):
            return datetime(year, 1 if num == 1 else 7, 1), "S"
        if num == 3:
            return datetime(year, 1, 1), "A"
    elif kind == "A":
        return datetime(year, 1, 1), "A"
    return None, "?"


def _to_float(value: object) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return float("nan")  # BLS uses "-" / "(NA)" for unavailable values


def _observation_row(series_id: str, obs: dict[str, Any]) -> dict[str, Any] | None:
    """One tidy row from a BLS observation, or ``None`` for an unknown period."""
    year, period = int(obs["year"]), obs["period"]
    day, freq = parse_period(year, period)
    if day is None:
        log.debug("Skipping unknown period %s for %s", period, series_id)
        return None
    notes = "; ".join(f.get("text", "") for f in obs.get("footnotes", []) if f and f.get("text"))
    return {
        "series_id": series_id,
        "date": day,
        "year": year,
        "period": period,
        "period_name": obs.get("periodName", ""),
        "freq": freq,
        "value": _to_float(obs.get("value")),
        "footnotes": notes,
        "source": "BLS",
    }


def parse_bls_response(
    payload: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], list[str]]:
    """Turn a BLS v2 JSON payload into ``(tidy frame, catalog by series, messages)``.

    Raises:
        BLSError: When the request did not succeed.
    """
    status = payload.get("status")
    messages = [m for m in payload.get("message", []) if m]
    if status != "REQUEST_SUCCEEDED":
        raise BLSError(f"BLS request failed ({status}): {'; '.join(messages) or 'no message'}")
    rows: list[dict[str, Any]] = []
    catalogs: dict[str, dict[str, Any]] = {}
    for series in payload.get("Results", {}).get("series", []):
        series_id = series.get("seriesID")
        if series.get("catalog"):
            catalogs[series_id] = series["catalog"]
        for obs in series.get("data", []):
            row = _observation_row(series_id, obs)
            if row is not None:
                rows.append(row)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(["series_id", "date", "freq"]).reset_index(drop=True)
    return frame, catalogs, messages


def year_windows(start: int, end: int, max_years: int) -> list[tuple[int, int]]:
    """Split ``start..end`` into windows of at most ``max_years`` years."""
    windows, year = [], start
    while year <= end:
        windows.append((year, min(year + max_years - 1, end)))
        year += max_years
    return windows


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class BLSClient:
    """BLS API client with batching and a per-series disk cache.

    Args:
        api_key: Registration key; defaults to ``BLS_API_KEY``.
        session: HTTP session; the gateway passes its guarded session.
        cache: Disk cache; defaults to ``CACHE_DIR``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        cache: DiskCache | None = None,
    ):
        self.api_key = api_key if api_key is not None else get_source_settings().bls_api_key
        self.session = session or make_session()
        self.cache = cache if cache is not None else default_cache()
        self.queries_made = 0
        self.last_messages: list[str] = []

    @property
    def registered(self) -> bool:
        """Whether requests carry a registration key."""
        return bool(self.api_key)

    def _post(
        self, series_ids: list[str], start: int, end: int, *, annual_average: bool, catalog: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "seriesid": series_ids, "startyear": str(start), "endyear": str(end),
        }  # fmt: skip
        if self.api_key:
            body["registrationkey"] = self.api_key
            if annual_average:
                body["annualaverage"] = True
            if catalog:
                body["catalog"] = True
        response = self.session.post(BLS_URL, json=body, timeout=DEFAULT_TIMEOUT)
        self.queries_made += 1
        response.raise_for_status()
        return response.json()

    def fetch(
        self,
        series_ids: str | Iterable[str],
        start_year: int,
        end_year: int | None = None,
        *,
        annual_average: bool = False,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch one or more BLS series as a tidy long frame (``COLUMNS``).

        ``annual_average`` needs a registered key (BLS ignores it otherwise); annual-average
        rows have ``freq == "A"``. Series with no data are not cached (typos, unreleased data).

        Raises:
            ValueError: When ``start_year`` is after ``end_year``.
            BLSError: When BLS refuses the request.
        """
        ids = [series_ids] if isinstance(series_ids, str) else list(dict.fromkeys(series_ids))
        ids = [s.strip().upper() for s in ids if s and s.strip()]
        end_year = end_year or date.today().year
        if start_year > end_year:
            raise ValueError(f"start_year {start_year} > end_year {end_year}")

        def parts(series_id: str) -> dict[str, Any]:
            return {
                "series_id": series_id, "start": start_year, "end": end_year,
                "annual_average": annual_average,
            }  # fmt: skip

        frames: list[pd.DataFrame] = []
        missing: list[str] = []
        for series_id in ids:
            cached = self.cache.get("bls", parts(series_id)) if use_cache else None
            if cached is not None:
                frames.append(cached)
            else:
                missing.append(series_id)
        self.last_messages = []
        if missing:
            fetched = self._fetch_missing(missing, start_year, end_year, annual_average)
            for series_id in missing:
                part = fetched.loc[fetched["series_id"] == series_id].reset_index(drop=True)
                if part.empty:
                    log.warning(
                        "BLS returned no data for %s (%s-%s)", series_id, start_year, end_year
                    )
                    continue
                if use_cache:
                    self.cache.put("bls", parts(series_id), part)
                frames.append(part)

        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=COLUMNS)
        out = pd.concat(frames, ignore_index=True)
        out["date"] = pd.to_datetime(out["date"])
        return out.sort_values(["series_id", "date", "freq"]).reset_index(drop=True)

    def _fetch_missing(
        self, series_ids: list[str], start_year: int, end_year: int, annual_average: bool
    ) -> pd.DataFrame:
        """Query BLS in batches of ``LIMITS`` (series × years); collects status messages."""
        max_series, max_years = LIMITS[self.registered]
        fetched: list[pd.DataFrame] = []
        for group in _chunks(series_ids, max_series):
            for w_start, w_end in year_windows(start_year, end_year, max_years):
                payload = self._post(
                    group, w_start, w_end, annual_average=annual_average, catalog=False
                )
                frame, _, messages = parse_bls_response(payload)
                self.last_messages.extend(messages)
                for message in messages:
                    log.info("BLS: %s", message)
                fetched.append(frame)
        new = pd.concat(fetched, ignore_index=True) if fetched else pd.DataFrame(columns=COLUMNS)
        return new.drop_duplicates(["series_id", "period", "year"], keep="last")

    def series_info(self, series_ids: str | Iterable[str]) -> dict[str, dict[str, Any]]:
        """Catalog metadata (title, survey, seasonality...) by series. Needs a registered key.

        Raises:
            BLSError: Without a key, or when BLS refuses the request.
        """
        if not self.registered:
            raise BLSError("BLS catalog metadata requires BLS_API_KEY.")
        ids = [series_ids] if isinstance(series_ids, str) else list(series_ids)
        year = date.today().year
        info: dict[str, dict[str, Any]] = {}
        for group in _chunks(ids, LIMITS[True][0]):
            payload = self._post(group, year - 1, year, annual_average=False, catalog=True)
            _, catalogs, _ = parse_bls_response(payload)
            info.update(catalogs)
        return info
