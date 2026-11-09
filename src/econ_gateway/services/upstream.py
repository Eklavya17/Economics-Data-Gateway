"""Access to BLS/FRED data through the ``sources`` clients, behind a small interface.

Services depend on the ``Upstream`` protocol rather than on the clients directly, so tests
substitute an in-memory fake and never touch the network. ``LiveUpstream`` is the real
implementation. Around the clients it adds (Phase 4, DESIGN.md §9):

- **guarded HTTP**: every BLS/FRED request passes the allow-list, rate limit, BLS daily quota and
  circuit breaker (``infra/http.py``, configured in ``services/reliability.py``);
- **release-aware caching**: cached observations expire when their release comes out, not after
  a fixed 12 h (``infra/cache.py`` + ``services/release_calendar.py``);
- **request merging**: identical concurrent fetches run once (``infra/single_flight.py``);
- **stale fallback**: when BLS or FRED cannot be reached, the last cached copy is returned with a
  ``stale_as_of`` column instead of an error;
- **error translation**: library exceptions become ``GatewayError`` subclasses whose messages
  tell the client model what to do next.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

import pandas as pd
import requests

from econ_gateway.config import get_settings
from econ_gateway.consts import METADATA_CACHE_TTL_S
from econ_gateway.infra.cache import ReleaseAwareCache
from econ_gateway.infra.errors import GuardRejected, QuotaExhaustedError
from econ_gateway.infra.http import offline_session
from econ_gateway.infra.single_flight import SingleFlight
from econ_gateway.infra.telemetry import span
from econ_gateway.models import Source
from econ_gateway.resolve.retrieval import CatalogRetriever
from econ_gateway.services.errors import ConfigurationError, SeriesNotFoundError, UpstreamError
from econ_gateway.services.release_calendar import (
    ReleaseCalendar,
    fetch_release_dates,
    fetch_series_release,
)
from econ_gateway.services.reliability import Reliability, build_reliability
from econ_gateway.services.ttl_cache import TTLCache
from econ_gateway.services.upstream_places import OewsLevel, PlacesUpstreamMixin
from econ_gateway.sources import BLSClient, BLSError, FREDClient, FREDError, infer_source
from econ_gateway.sources.catalog import load_catalog
from econ_gateway.sources.settings import get_source_settings

log = logging.getLogger(__name__)

T = TypeVar("T")

FRED_KEY_HINT = (
    "FRED_API_KEY is not set on the gateway server, so FRED series and FRED search are "
    "unavailable. The server operator can get a free key at "
    "https://fred.stlouisfed.org/docs/api/api_key.html."
)
BLS_QUOTA_HINT = (
    "FRED carries copies of many BLS series (e.g. UNRATE, PAYEMS, CPIAUCSL): call search_series "
    "to find one."
)
NO_CACHED_COPY = " No cached copy of this data is available."
# Optional column on frames returned by fetch_bls / fetch_fred (see the Upstream protocol).
STALE_COLUMN = "stale_as_of"


class Upstream(Protocol):
    """Everything the services need from BLS, FRED and the local catalog.

    Observation frames from ``fetch_bls`` and ``fetch_fred`` may carry an optional
    ``stale_as_of`` column (ISO UTC timestamp). It is set only when the upstream could not be
    reached and the rows are the last cached copy, fetched at that time.
    """

    @property
    def has_fred_key(self) -> bool:
        """Whether FRED requests can be made."""
        ...

    @property
    def has_bls_key(self) -> bool:
        """Whether BLS requests use a registered key (higher limits, series metadata)."""
        ...

    def catalog(self) -> pd.DataFrame:
        """The curated series catalog (one row per validated series)."""
        ...

    def retriever(self) -> CatalogRetriever:
        """A BM25 retriever over the catalog."""
        ...

    def infer_source(self, series_id: str) -> Source:
        """Which system publishes ``series_id``."""
        ...

    def fetch_bls(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> tuple[pd.DataFrame, list[str]]:
        """Fetch BLS observations; returns (long frame, BLS status messages)."""
        ...

    def fetch_fred(self, series_id: str, start: str, end: str) -> pd.DataFrame:
        """Fetch FRED observations between ISO dates."""
        ...

    def search_fred(self, text: str, limit: int) -> pd.DataFrame:
        """FRED full-text series search."""
        ...

    def fred_series_info(self, series_id: str) -> dict[str, Any]:
        """FRED series metadata."""
        ...

    def bls_series_info(self, series_id: str) -> dict[str, Any] | None:
        """BLS catalog metadata, or ``None`` when unavailable (no key, or unknown series)."""
        ...

    # ------------------------------------------------------------ releases, health (Phase 5)

    def release_id(self, series_id: str, source: Source) -> int | None:
        """FRED release ID of the release that publishes the series; ``None`` when unknown."""
        ...

    def release_dates(self, release_id: int) -> list[date]:
        """Recent and scheduled dates of a release, ascending; empty when unavailable."""
        ...

    def status(self) -> dict[str, object]:
        """Per-upstream health (breakers, rate limits, quota). Never contains secrets."""
        ...

    # ------------------------------------------------------------ places (Phase 2)

    def oews_areas(self) -> pd.DataFrame:
        """OEWS areas: ``area``, ``area_title``, ``prim_state`` and ``level``."""
        ...

    def qcew_areas(self) -> pd.DataFrame:
        """QCEW areas: ``area_fips``, ``area_title``."""
        ...

    def occupations(self) -> pd.DataFrame:
        """SOC occupations in OEWS: ``occ_code``, ``occ_title``, ``o_group``."""
        ...

    def oews(self, year: int, level: OewsLevel) -> pd.DataFrame:
        """Cleaned OEWS table for May ``year``.

        Raises:
            DataNotAvailableError: When BLS has not published that year.
        """
        ...

    def qcew_area(self, year: int, quarter: int, area_code: str) -> pd.DataFrame:
        """All QCEW rows (industries × ownerships) for one area and quarter.

        Raises:
            DataNotAvailableError: When that quarter is not published (or the area is unknown).
        """
        ...


def _iso_utc(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, UTC).isoformat(timespec="seconds")


class LiveUpstream(PlacesUpstreamMixin):
    """``Upstream`` backed by the ``sources`` clients plus the Phase 4 guards.

    Observations are cached on disk in ``CACHE_DIR``, expiring on the release calendar.
    Series metadata is cached in memory for ``METADATA_CACHE_TTL_S``. The catalog and its
    retriever load once per process.
    Place data (OEWS, QCEW, area tables) comes from ``PlacesUpstreamMixin``.

    Args:
        reliability: Guards and sessions; defaults to ``build_reliability`` with the
            settings' ``state_dir``. Tests pass one with a fake transport.
        cache_dir: Observation cache directory; defaults to ``CACHE_DIR``.
    """

    def __init__(self, reliability: Reliability | None = None, cache_dir: Path | None = None):
        super().__init__()
        source_settings = get_source_settings()
        self.reliability = reliability or build_reliability(
            has_bls_key=self.has_bls_key,
            state_dir=get_settings().state_dir,
            user_agent=source_settings.bls_user_agent,
        )
        self.calendar = ReleaseCalendar(self._load_release_dates, self._load_series_release)
        self._cache_dir = cache_dir or source_settings.cache_dir
        self._cache_ttl_hours = source_settings.cache_ttl_hours
        self._cache = ReleaseAwareCache(
            self._cache_dir, self._cache_ttl_hours, is_fresh=self.calendar.is_fresh
        )
        self._flights = SingleFlight()
        self._catalog: pd.DataFrame | None = None
        self._retriever: CatalogRetriever | None = None
        self._meta_cache = TTLCache(METADATA_CACHE_TTL_S)

    # ------------------------------------------------------------ configuration

    @property
    def has_fred_key(self) -> bool:
        """Whether ``FRED_API_KEY`` is set."""
        return bool(os.getenv("FRED_API_KEY", "").strip())

    @property
    def has_bls_key(self) -> bool:
        """Whether ``BLS_API_KEY`` is set."""
        return bool(os.getenv("BLS_API_KEY", "").strip())

    def status(self) -> dict[str, object]:
        """Breakers, rate-limit tokens, BLS quota and in-flight fetches (for logs and health)."""
        return {**self.reliability.snapshot(), "in_flight_fetches": self._flights.in_flight}

    def release_id(self, series_id: str, source: Source) -> int | None:
        """Release lookup via the calendar (BLS: ID prefix; FRED: ``series/release``)."""
        return self.calendar.release_id("bls" if source == "BLS" else "fred", series_id)

    def release_dates(self, release_id: int) -> list[date]:
        """Release dates from the calendar (cached; empty when FRED is unavailable)."""
        return self.calendar.release_dates(release_id)

    # ------------------------------------------------------------ catalog

    def catalog(self) -> pd.DataFrame:
        """Load the catalog once per process."""
        if self._catalog is None:
            self._catalog = load_catalog()
        return self._catalog

    def retriever(self) -> CatalogRetriever:
        """Build the BM25 index once per process (~300 rows, a few milliseconds)."""
        if self._retriever is None:
            self._retriever = CatalogRetriever(self.catalog())
        return self._retriever

    def infer_source(self, series_id: str) -> Source:
        """Catalog lookup first, then BLS ID-prefix rules (``sources.series.infer_source``)."""
        return "BLS" if infer_source(series_id, self.catalog()) == "BLS" else "FRED"

    # ------------------------------------------------------------ BLS observations

    def fetch_bls(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> tuple[pd.DataFrame, list[str]]:
        """Fetch via ``BLSClient`` (batches 50 series / 20 years per query with a key).

        Falls back to cached copies per series when BLS cannot be reached.
        """
        key = ("bls", tuple(sorted(series_ids)), start_year, end_year)
        attributes = {"bls.series_count": len(series_ids), "bls.years": f"{start_year}-{end_year}"}
        with span("upstream.bls.fetch", **attributes) as current:
            result = self._merged(
                key, lambda: self._fetch_bls_now(series_ids, start_year, end_year)
            )
            current.set_attribute("econ.stale", STALE_COLUMN in result[0].columns)
        return result[0].copy(), list(result[1])

    def _fetch_bls_now(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> tuple[pd.DataFrame, list[str]]:
        client = BLSClient(session=self.reliability.session(), cache=self._cache)
        started = time.perf_counter()
        try:
            frame = client.fetch(series_ids, start_year, end_year, annual_average=False)
            return frame, list(client.last_messages)
        except (BLSError, requests.RequestException) as exc:
            if isinstance(exc, BLSError) and "threshold" in str(exc).lower():
                # BLS says the daily quota is spent even if our count disagrees (shared key).
                self.reliability.bls_quota.mark_exhausted()
            stale = self._stale_bls(series_ids, start_year, end_year)
            if stale is None:
                raise self._bls_error(exc) from exc
            log.warning("serving stale BLS data", extra={"series": len(series_ids)})
            return stale, []
        finally:
            log.info(
                "upstream call",
                extra={
                    "upstream": "BLS",
                    "series": len(series_ids),
                    "queries": client.queries_made,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                },
            )

    def _stale_bls(
        self, series_ids: list[str], start_year: int, end_year: int
    ) -> pd.DataFrame | None:
        """Last cached copy of each series for the same years; ``None`` if none is cached."""
        cache = self._stale_cache()
        client = BLSClient(session=offline_session(), cache=cache)
        frames: list[pd.DataFrame] = []
        for series_id in series_ids:  # one at a time: a missing series must not hide the others
            try:
                part = client.fetch([series_id], start_year, end_year, annual_average=False)
            except (BLSError, requests.RequestException):
                continue
            frames.append(part.assign(**{STALE_COLUMN: _iso_utc(cache.served[series_id])}))
        return pd.concat(frames, ignore_index=True) if frames else None

    @staticmethod
    def _bls_error(exc: Exception) -> UpstreamError:
        if isinstance(exc, QuotaExhaustedError):
            return UpstreamError(f"{exc.advice}{NO_CACHED_COPY} {BLS_QUOTA_HINT}")
        if isinstance(exc, GuardRejected):
            return UpstreamError(f"{exc.advice}{NO_CACHED_COPY}")
        if isinstance(exc, BLSError):
            return UpstreamError(
                f"BLS refused the request: {exc}. If this mentions a daily threshold, the "
                f"gateway's BLS quota is used up; try again tomorrow. {BLS_QUOTA_HINT}"
            )
        return UpstreamError(
            f"Could not reach the BLS API ({type(exc).__name__}) and no cached copy is "
            "available. Retry in a minute."
        )

    # ------------------------------------------------------------ FRED observations

    def fetch_fred(self, series_id: str, start: str, end: str) -> pd.DataFrame:
        """Fetch via ``FREDClient.observations``; falls back to the cached copy on failure."""
        with span("upstream.fred.fetch", **{"fred.series_id": series_id}) as current:
            frame = self._merged(
                ("fred", series_id, start, end),
                lambda: self._fetch_fred_now(series_id, start, end),
            )
            current.set_attribute("econ.stale", STALE_COLUMN in frame.columns)
        return frame.copy()

    def _fetch_fred_now(self, series_id: str, start: str, end: str) -> pd.DataFrame:
        client = self._fred_client()
        started = time.perf_counter()
        try:
            return client.observations(series_id, start, end)
        except FREDError as exc:
            translated = self._translate_fred_error(series_id, exc)
            if isinstance(translated, SeriesNotFoundError):
                raise translated from exc
            failure: Exception = exc
        except requests.RequestException as exc:
            failure = exc
        finally:
            log.info(
                "upstream call",
                extra={
                    "upstream": "FRED",
                    "series_id": series_id,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                },
            )
        stale = self._stale_fred(series_id, start, end)
        if stale is None:
            raise self._fred_error(failure, NO_CACHED_COPY) from failure
        log.warning("serving stale FRED data", extra={"series_id": series_id})
        return stale

    def _stale_fred(self, series_id: str, start: str, end: str) -> pd.DataFrame | None:
        cache = self._stale_cache()
        client = FREDClient(session=offline_session(), cache=cache)
        try:
            frame = client.observations(series_id, start, end)
        except (FREDError, requests.RequestException):
            return None
        return frame.assign(**{STALE_COLUMN: _iso_utc(cache.served[series_id])})

    # ------------------------------------------------------------ FRED search and metadata

    def search_fred(self, text: str, limit: int) -> pd.DataFrame:
        """FRED full-text search ordered by popularity (not cached; merged when concurrent)."""
        client = self._fred_client()

        def run() -> pd.DataFrame:
            try:
                return client.search(text, limit=limit)
            except (FREDError, requests.RequestException) as exc:
                raise self._fred_error(exc) from exc

        return self._merged(("fred-search", text, limit), run).copy()

    def fred_series_info(self, series_id: str) -> dict[str, Any]:
        """FRED ``series`` endpoint, cached in memory."""
        key = f"fred:{series_id}"
        cached = self._meta_cache.get(key)
        if cached is not None:
            return cached
        client = self._fred_client()
        try:
            info = client.series_info(series_id)
        except FREDError as exc:
            raise self._translate_fred_error(series_id, exc) from exc
        except requests.RequestException as exc:
            raise self._fred_error(exc) from exc
        self._meta_cache.put(key, info)
        return info

    def bls_series_info(self, series_id: str) -> dict[str, Any] | None:
        """BLS catalog metadata (needs a key; costs one BLS query), cached in memory.

        Skipped when the BLS quota is down to its reserve: metadata is optional (the catalog
        and FRED cover most series), observations are not.
        """
        if not self.has_bls_key:
            return None
        key = f"bls:{series_id}"
        cached = self._meta_cache.get(key)
        if cached is not None:
            return cached
        try:
            self.reliability.bls_quota.check(reserve=self.reliability.bls_quota_reserve)
            client = BLSClient(session=self.reliability.session(), cache=self._cache)
            info = client.series_info(series_id).get(series_id)
        except (BLSError, requests.RequestException) as exc:
            log.warning(
                "BLS metadata lookup skipped or failed",
                extra={"series_id": series_id, "err": type(exc).__name__},
            )
            return None
        if info:
            self._meta_cache.put(key, info)
        return info

    # ------------------------------------------------------------ release calendar loaders

    def _load_release_dates(self, release_id: int, start: date, end: date) -> list[date]:
        if not self.has_fred_key:
            return []
        session = self.reliability.session()
        return fetch_release_dates(session, self._fred_key(), release_id, start, end)

    def _load_series_release(self, series_id: str) -> int | None:
        if not self.has_fred_key:
            return None
        return fetch_series_release(self.reliability.session(), self._fred_key(), series_id)

    # ------------------------------------------------------------ helpers

    def _merged(self, key: tuple[object, ...], fn: Callable[[], T]) -> T:
        """Run ``fn`` once for concurrent identical requests (callers copy shared frames)."""
        result, shared = self._flights.do(key, fn)
        if shared:
            log.info("merged identical request", extra={"key": str(key[:2])})
        return result

    def _stale_cache(self) -> ReleaseAwareCache:
        """A fresh stale-mode cache (its ``served`` record belongs to one fallback only)."""
        return ReleaseAwareCache(self._cache_dir, self._cache_ttl_hours, stale_ok=True)

    def _fred_client(self) -> FREDClient:
        if not self.has_fred_key:
            raise ConfigurationError(FRED_KEY_HINT)
        return FREDClient(session=self.reliability.session(), cache=self._cache)

    @staticmethod
    def _fred_key() -> str:
        return os.getenv("FRED_API_KEY", "").strip()

    @staticmethod
    def _fred_error(exc: Exception, suffix: str = "") -> UpstreamError:
        """Message for a FRED failure; ``suffix`` is an extra sentence (e.g. ``NO_CACHED_COPY``)."""
        if isinstance(exc, GuardRejected):
            return UpstreamError(f"{exc.advice}{suffix}")
        if isinstance(exc, FREDError):
            return UpstreamError(f"FRED request failed: {exc}.{suffix}")
        return UpstreamError(
            f"Could not reach the FRED API ({type(exc).__name__}). Retry in a minute.{suffix}"
        )

    @staticmethod
    def _translate_fred_error(series_id: str, exc: FREDError) -> Exception:
        """Map FRED's 400 answers for unknown/malformed IDs to not-found; the rest is upstream.

        FRED says "The series does not exist" for well-formed unknown IDs and "Invalid value for
        variable series_id" for malformed ones; both mean the caller needs a different ID.
        """
        text = str(exc).lower()
        if any(
            s in text
            for s in ("does not exist", "no metadata", "invalid value for variable series_id")
        ):
            return SeriesNotFoundError(
                f"FRED has no series '{series_id}'. Call search_series with a description of "
                "the data you need to get a valid ID."
            )
        return UpstreamError(f"FRED request failed: {exc}")
