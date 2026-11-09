"""The HTTP layer every BLS/FRED request goes through: allow-list, rate limit, quota, breaker.

The ``sources`` ``BLSClient`` and ``FREDClient`` accept a ``requests.Session``. The gateway gives
them a session whose transport adapter is a ``GuardedAdapter``, so every request they make
(including each 50-series BLS batch) passes the same checks, in this order:

1. **Allow-list** (CLAUDE.md rule 7): HTTPS to a known host only, redirects included.
2. **Daily quota** (BLS): refuse locally once the day's queries are spent.
3. **Token bucket**: wait briefly for a slot; give up with a "busy" error rather than hang.
4. **Circuit breaker**: fail fast while the upstream is known to be down.
5. **Timeout cap**: the clients ask for 60 s; the gateway caps connect/read time so that a
   retry still fits inside the tool timeout.

The wrapped ("inner") adapter does the actual I/O with urllib3 retries, so a retried request
counts once against the bucket and the breaker sees only the final outcome.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter, HTTPAdapter
from requests.models import PreparedRequest, Response
from urllib3.util.retry import Retry

from econ_gateway.infra.breaker import CircuitBreaker
from econ_gateway.infra.errors import DisallowedHostError, GuardRejected, RateLimitedError
from econ_gateway.infra.quota import DailyQuota
from econ_gateway.infra.rate_limit import TokenBucket
from econ_gateway.infra.telemetry import span

log = logging.getLogger(__name__)

# Same shapes as requests' own (private) TimeoutType / CertType annotations on BaseAdapter.send.
Timeout = float | tuple[float | None, float | None] | None
Cert = str | tuple[str, str] | None


@dataclass
class UpstreamGuard:
    """The protections for one upstream host.

    Attributes:
        name: Upstream name used in messages and logs (``BLS``, ``FRED``).
        bucket: Request-rate limiter.
        breaker: Circuit breaker.
        max_wait_s: Longest a request waits for a rate-limit token before failing.
        quota: Daily query quota, for upstreams that have one.
    """

    name: str
    bucket: TokenBucket
    breaker: CircuitBreaker
    max_wait_s: float
    quota: DailyQuota | None = None

    def snapshot(self) -> dict[str, object]:
        """State for status reports and logs."""
        status: dict[str, object] = {
            "circuit": self.breaker.snapshot(),
            "rate_tokens_available": round(self.bucket.available, 1),
        }
        if self.quota is not None:
            status["daily_quota"] = self.quota.snapshot()
        return status


def _is_failure(response: Response) -> bool:
    """429 and 5xx mean the upstream is overloaded or down; other codes mean it answered."""
    return response.status_code == 429 or response.status_code >= 500


class GuardedAdapter(BaseAdapter):
    """Transport adapter that applies an ``UpstreamGuard`` before delegating to ``inner``.

    Args:
        guards: Guard per allowed host name (e.g. ``{"api.bls.gov": bls_guard}``).
        inner: Adapter that performs the I/O (``make_inner_adapter()`` in production; tests pass
            a fake).
        timeout_s: ``(connect, read)`` caps in seconds.
    """

    def __init__(
        self,
        guards: Mapping[str, UpstreamGuard],
        *,
        inner: BaseAdapter,
        timeout_s: tuple[float, float],
    ):
        super().__init__()
        self._guards = dict(guards)
        self._inner = inner
        self._timeout_s = timeout_s

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: Cert = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        """Check the guard for the request's host, send it, and record the outcome.

        Raises:
            DisallowedHostError: Plain HTTP, or a host outside the allow-list.
            QuotaExhaustedError: The upstream's daily quota is used up.
            RateLimitedError: No rate-limit token became available within ``max_wait_s``.
            CircuitOpenError: The upstream is failing and requests are paused.
            requests.RequestException: Network failures from the inner adapter.
        """
        guard = self._guard_for(request.url or "")
        # No URL in the span: FRED puts the API key in the query string.
        with span(
            "upstream.http", **{"upstream.name": guard.name, "http.request.method": request.method}
        ) as current:
            try:
                self._admit(guard)
            except GuardRejected as exc:
                current.set_attribute("econ.guard.rejected", type(exc).__name__)
                raise
            started = time.perf_counter()
            try:
                response = self._inner.send(
                    request,
                    stream=stream,
                    timeout=self._cap(timeout),
                    verify=verify,
                    cert=cert,
                    proxies=proxies,
                )
            except BaseException:
                # Any exception, not just network errors: an admitted half-open probe must
                # always be resolved, or the breaker would refuse every later request.
                guard.breaker.record_failure()
                log.warning("upstream request failed", extra={"upstream": guard.name})
                raise
            current.set_attribute("http.response.status_code", response.status_code)
            if _is_failure(response):
                guard.breaker.record_failure()
            else:
                guard.breaker.record_success()
            log.debug(
                "upstream http",
                extra={
                    "upstream": guard.name,
                    "status": response.status_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return response

    @staticmethod
    def _admit(guard: UpstreamGuard) -> None:
        """Quota, rate limit and breaker checks, in that order; spends quota only if admitted."""
        if guard.quota is not None:
            guard.quota.check()
        if not guard.bucket.acquire(guard.max_wait_s):
            raise RateLimitedError(
                guard.name,
                f"The gateway is sending {guard.name} as many requests as it allows right now. "
                "Retry in a few seconds, or ask for fewer series per call.",
                guard.max_wait_s,
            )
        guard.breaker.before_call()
        if guard.quota is not None:
            guard.quota.consume()

    def close(self) -> None:
        """Close the inner adapter's connection pools."""
        self._inner.close()

    def _guard_for(self, url: str) -> UpstreamGuard:
        parts = urlsplit(url)
        guard = self._guards.get(parts.hostname or "")
        if parts.scheme != "https" or guard is None:
            raise DisallowedHostError(
                f"Refusing request to {parts.scheme}://{parts.hostname}: the gateway only calls "
                f"its allow-listed upstreams over HTTPS ({', '.join(sorted(self._guards))})."
            )
        return guard

    def _cap(self, timeout: Timeout) -> tuple[float, float]:
        """Apply the gateway's ``(connect, read)`` caps to whatever the caller asked for."""
        connect_cap, read_cap = self._timeout_s
        if timeout is None:
            return connect_cap, read_cap
        if isinstance(timeout, tuple):
            connect, read = timeout
            return min(connect or connect_cap, connect_cap), min(read or read_cap, read_cap)
        return min(timeout, connect_cap), min(timeout, read_cap)


def make_inner_adapter(retries: int) -> HTTPAdapter:
    """Real I/O adapter with a short urllib3 retry policy.

    Retries cover connection errors and 5xx. Not 429: a rate-limited upstream should be left
    alone, and the breaker and token bucket handle that. ``Retry-After`` is ignored so a long
    server-requested pause cannot outlast the tool timeout.
    """
    retry = Retry(
        total=retries,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=False,
    )
    return HTTPAdapter(max_retries=retry)


@dataclass
class SessionFactory:
    """Hands out one ``requests.Session`` per worker thread, all sharing one ``GuardedAdapter``.

    ``requests.Session`` is not documented as thread-safe, while urllib3's connection pools
    (inside the adapter) are. Sharing the adapter keeps connection reuse and one set of guards.

    Attributes:
        adapter: The guarded adapter mounted on every session.
        user_agent: ``User-Agent`` header (bls.gov rejects anonymous scripts).
    """

    adapter: GuardedAdapter
    user_agent: str
    _local: threading.local = field(default_factory=threading.local, repr=False)

    def session(self) -> requests.Session:
        """The calling thread's session, created on first use."""
        session: requests.Session | None = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            # Mount on http:// too, so plain-HTTP URLs reach the adapter and are refused there.
            session.mount("https://", self.adapter)
            session.mount("http://", self.adapter)
            session.headers["User-Agent"] = self.user_agent
            self._local.session = session
        return session


class OfflineAdapter(BaseAdapter):
    """Adapter that never sends: used to read stale cache entries without touching the network."""

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: Cert = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        """Always raise: the caller only wanted what the cache already holds."""
        raise requests.ConnectionError("offline: serving cached data only")

    def close(self) -> None:
        """Nothing to close."""


def offline_session() -> requests.Session:
    """A session whose every request fails immediately (see ``OfflineAdapter``)."""
    session = requests.Session()
    session.mount("https://", OfflineAdapter())
    session.mount("http://", OfflineAdapter())
    return session
