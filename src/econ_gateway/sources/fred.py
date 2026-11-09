"""FRED (Federal Reserve Economic Data) API client.

Docs: https://fred.stlouisfed.org/docs/api/fred/ . Needs a free API key (``FRED_API_KEY``).

Origin: Project 1 (``labor_agent.data.fred``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests

from econ_gateway.sources.cache import DiskCache, default_cache
from econ_gateway.sources.http import DEFAULT_TIMEOUT, make_session
from econ_gateway.sources.settings import get_source_settings

FRED_BASE = "https://api.stlouisfed.org/fred"
COLUMNS = ["series_id", "date", "value", "source"]
SEARCH_COLUMNS = [
    "id", "title", "frequency_short", "units_short", "seasonal_adjustment_short",
    "observation_start", "observation_end", "popularity", "last_updated",
]  # fmt: skip


class FREDError(RuntimeError):
    """FRED refused or failed the request; the message is FRED's ``error_message``."""


def parse_observations(series_id: str, payload: dict[str, Any]) -> pd.DataFrame:
    """Tidy frame from a ``series/observations`` payload (``"."`` = missing becomes NaN)."""
    obs = payload.get("observations", [])
    frame = pd.DataFrame(
        {
            "series_id": series_id,
            "date": pd.to_datetime([o["date"] for o in obs]),
            "value": pd.to_numeric([o["value"] for o in obs], errors="coerce"),
            "source": "FRED",
        },
        columns=COLUMNS,
    )
    return frame.reset_index(drop=True)


class FREDClient:
    """FRED API client with a disk cache for observations.

    Args:
        api_key: Defaults to ``FRED_API_KEY``.
        session: HTTP session; the gateway passes its guarded session.
        cache: Disk cache; defaults to ``CACHE_DIR``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        cache: DiskCache | None = None,
    ):
        self.api_key = api_key if api_key is not None else get_source_settings().fred_api_key
        self.session = session or make_session()
        self.cache = cache if cache is not None else default_cache()

    def _get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        """GET a FRED endpoint. The URL (which carries the key) never appears in errors."""
        if not self.api_key:
            raise FREDError(
                "FRED_API_KEY is not set. Get a free key at "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        query = {k: v for k, v in params.items() if v is not None}
        query.update(api_key=self.api_key, file_type="json")
        response = self.session.get(
            f"{FRED_BASE}/{endpoint}", params=query, timeout=DEFAULT_TIMEOUT
        )
        if response.status_code >= 400:
            try:
                message = response.json().get("error_message", response.text)
            except ValueError:
                message = response.text
            raise FREDError(f"FRED {endpoint} failed ({response.status_code}): {message}")
        return response.json()

    def observations(
        self,
        series_id: str,
        start: str | None = None,
        end: str | None = None,
        *,
        units: str | None = None,
        frequency: str | None = None,
        aggregation_method: str | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Observations between ISO dates (``YYYY-MM-DD``).

        Args:
            series_id: FRED series ID.
            start: First date, or ``None`` for the beginning.
            end: Last date, or ``None`` for the latest.
            units: FRED transform: ``lin`` (default), ``chg``, ``ch1``, ``pch``, ``pc1``...
            frequency: Aggregate to a lower frequency: ``d``, ``w``, ``m``, ``q``, ``sa``, ``a``.
            aggregation_method: How to aggregate (``avg``, ``sum``, ``eop``).
            use_cache: Read and write the disk cache.
        """
        series_id = series_id.strip().upper()
        parts = {
            "series_id": series_id, "start": start, "end": end, "units": units,
            "frequency": frequency, "agg": aggregation_method,
        }  # fmt: skip
        if use_cache:
            cached = self.cache.get("fred", parts)
            if cached is not None:
                return cached
        payload = self._get(
            "series/observations",
            series_id=series_id,
            observation_start=start,
            observation_end=end,
            units=units,
            frequency=frequency,
            aggregation_method=aggregation_method,
        )
        frame = parse_observations(series_id, payload)
        if use_cache and not frame.empty:
            self.cache.put("fred", parts, frame)
        return frame

    def series_info(self, series_id: str) -> dict[str, Any]:
        """Metadata from the ``series`` endpoint.

        Raises:
            FREDError: Unknown series, or FRED failure.
        """
        payload = self._get("series", series_id=series_id.strip().upper())
        items = payload.get("seriess", [])
        if not items:
            raise FREDError(f"No metadata for {series_id}")
        return items[0]

    def search(self, text: str, limit: int = 20, order_by: str = "popularity") -> pd.DataFrame:
        """Full-text series search, most popular first."""
        payload = self._get(
            "series/search", search_text=text, limit=limit, order_by=order_by, sort_order="desc"
        )
        return pd.DataFrame(payload.get("seriess", []), columns=SEARCH_COLUMNS)
