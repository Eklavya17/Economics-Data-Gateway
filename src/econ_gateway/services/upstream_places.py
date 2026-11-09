"""Place-level data access (OEWS wages, QCEW area files, area/occupation tables) via ``sources``.

Mixed into ``LiveUpstream``. Two caches keep tool calls fast:

- OEWS tables are parsed once (``sources/oews.py`` caches them as parquet) and kept in memory;
  the metro table is ~140k rows.
- "Not published" answers (OEWS 404 for a future year, QCEW 404 for a future quarter) are
  remembered for ``AVAILABILITY_CACHE_TTL_S`` so year/quarter discovery does not repeat them.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

import pandas as pd
import requests

from econ_gateway.consts import AVAILABILITY_CACHE_TTL_S
from econ_gateway.services.errors import DataNotAvailableError, UpstreamError
from econ_gateway.services.ttl_cache import TTLCache
from econ_gateway.sources import OEWSError, QCEWError, fetch_qcew, load_oews
from econ_gateway.sources.catalog import load_occupations, load_oews_areas, load_qcew_areas

log = logging.getLogger(__name__)

OewsLevel = Literal["national", "state", "metro"]


class PlacesUpstreamMixin:
    """OEWS / QCEW / reference-table methods of the ``Upstream`` protocol."""

    def __init__(self) -> None:
        self._oews_tables: dict[tuple[int, str], pd.DataFrame] = {}
        self._unavailable = TTLCache(AVAILABILITY_CACHE_TTL_S)

    def oews_areas(self) -> pd.DataFrame:
        """OEWS area reference table (shipped in ``sources/reference``)."""
        return load_oews_areas()

    def qcew_areas(self) -> pd.DataFrame:
        """QCEW area reference table (shipped in ``sources/reference``)."""
        return load_qcew_areas()

    def occupations(self) -> pd.DataFrame:
        """SOC occupation reference table (shipped in ``sources/reference``)."""
        return load_occupations()

    def oews(self, year: int, level: OewsLevel) -> pd.DataFrame:
        """OEWS table for May ``year`` at ``level``, kept in memory after the first load.

        The first load of a year not yet on disk downloads the BLS zip (tens of MB) and parses
        Excel, which can take a minute; ``scripts/warm_cache.py`` does this ahead of time.
        """
        key = (year, level)
        if key in self._oews_tables:
            return self._oews_tables[key]
        cached_miss = self._unavailable.get(f"oews:{year}:{level}")
        if cached_miss:
            raise DataNotAvailableError(cached_miss)
        started = time.perf_counter()
        try:
            table = load_oews(year, level)
        except OEWSError as exc:
            message = f"OEWS May {year} ({level}) is not available: {exc}"
            self._unavailable.put(f"oews:{year}:{level}", message)
            raise DataNotAvailableError(message) from exc
        except requests.RequestException as exc:
            raise UpstreamError(
                f"Could not download OEWS May {year} from bls.gov ({type(exc).__name__})."
            ) from exc
        log.info(
            "upstream call",
            extra={
                "upstream": "OEWS",
                "year": year,
                "level": level,
                "rows": len(table),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        self._oews_tables[key] = table
        return table

    def qcew_area(self, year: int, quarter: int, area_code: str) -> pd.DataFrame:
        """QCEW area file for one quarter (disk-cached by ``sources/qcew.py`` for 30 days)."""
        miss_key = f"qcew:{year}:{quarter}:{area_code}"
        cached_miss = self._unavailable.get(miss_key)
        if cached_miss:
            raise DataNotAvailableError(cached_miss)
        try:
            return fetch_qcew(year, quarter, area=area_code)
        except QCEWError as exc:
            message = f"QCEW {year}-Q{quarter} is not published for area {area_code}."
            self._unavailable.put(miss_key, message)
            raise DataNotAvailableError(message) from exc
        except requests.RequestException as exc:
            raise UpstreamError(
                f"Could not reach the QCEW open-data API ({type(exc).__name__}). Retry shortly."
            ) from exc
