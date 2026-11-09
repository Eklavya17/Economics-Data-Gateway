"""Builds the per-upstream protections (rate limit, circuit breaker, BLS quota) from settings.

The mechanisms live in ``infra/``; this module only chooses their limits (``consts.py``) and
connects them to the two API hosts. ``LiveUpstream`` owns one ``Reliability`` per process,
so every tool call shares the same budgets and breaker state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.adapters import BaseAdapter

from econ_gateway.consts import (
    BLS_API_HOST,
    BLS_BURST,
    BLS_DAILY_QUOTA_ANONYMOUS,
    BLS_DAILY_QUOTA_REGISTERED,
    BLS_QUOTA_RESERVE_ANONYMOUS,
    BLS_QUOTA_RESERVE_REGISTERED,
    BLS_RATE_PER_S,
    BREAKER_FAILURE_THRESHOLD,
    BREAKER_RESET_TIMEOUT_S,
    FRED_API_HOST,
    FRED_BURST,
    FRED_RATE_PER_S,
    HTTP_CONNECT_TIMEOUT_S,
    HTTP_READ_TIMEOUT_S,
    HTTP_RETRIES,
    QUOTA_FILE_NAME,
    QUOTA_TIMEZONE,
    RATE_LIMIT_MAX_WAIT_S,
)
from econ_gateway.infra.breaker import CircuitBreaker
from econ_gateway.infra.http import (
    GuardedAdapter,
    SessionFactory,
    UpstreamGuard,
    make_inner_adapter,
)
from econ_gateway.infra.quota import DailyQuota
from econ_gateway.infra.rate_limit import TokenBucket


@dataclass
class Reliability:
    """Guards for the BLS and FRED APIs and the sessions that enforce them.

    Attributes:
        bls: BLS API guard (with the daily quota).
        fred: FRED API guard.
        sessions: Per-thread sessions sharing one ``GuardedAdapter``.
        bls_quota_reserve: BLS queries kept back for data requests (see ``DailyQuota.check``).
    """

    bls: UpstreamGuard
    fred: UpstreamGuard
    sessions: SessionFactory
    bls_quota_reserve: int

    @property
    def bls_quota(self) -> DailyQuota:
        """The BLS daily quota (always configured for the BLS guard)."""
        assert self.bls.quota is not None  # set by build_reliability
        return self.bls.quota

    def session(self) -> requests.Session:
        """The calling thread's guarded session."""
        return self.sessions.session()

    def snapshot(self) -> dict[str, object]:
        """Breaker states, rate-limit tokens and quota use, per upstream."""
        return {"BLS": self.bls.snapshot(), "FRED": self.fred.snapshot()}


def build_reliability(
    *,
    has_bls_key: bool,
    state_dir: Path | None,
    user_agent: str,
    inner: BaseAdapter | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Reliability:
    """Create the guards with the limits from ``consts.py``.

    Args:
        has_bls_key: Whether BLS requests use a registered key (500 vs 25 queries/day).
        state_dir: Where the BLS quota count is persisted; ``None`` keeps it in memory.
        user_agent: ``User-Agent`` for every request.
        inner: Adapter that performs I/O; tests pass a fake. Defaults to urllib3 with retries.
        clock: Monotonic clock for buckets and breakers (tests inject a fake).
        wall_clock: Epoch clock for the quota's day boundary.
        sleep: Sleep used while waiting for rate-limit tokens.

    Returns:
        The wired ``Reliability``.
    """

    def guard(name: str, rate_per_s: float, burst: int, quota: DailyQuota | None = None):
        return UpstreamGuard(
            name=name,
            bucket=TokenBucket(rate_per_s, burst, clock=clock, sleep=sleep),
            breaker=CircuitBreaker(
                name,
                failure_threshold=BREAKER_FAILURE_THRESHOLD,
                reset_timeout_s=BREAKER_RESET_TIMEOUT_S,
                clock=clock,
            ),
            max_wait_s=RATE_LIMIT_MAX_WAIT_S,
            quota=quota,
        )

    quota = DailyQuota(
        "BLS",
        limit=BLS_DAILY_QUOTA_REGISTERED if has_bls_key else BLS_DAILY_QUOTA_ANONYMOUS,
        path=state_dir / QUOTA_FILE_NAME if state_dir else None,
        tz=ZoneInfo(QUOTA_TIMEZONE),
        clock=wall_clock,
    )
    bls = guard("BLS", BLS_RATE_PER_S, BLS_BURST, quota)
    fred = guard("FRED", FRED_RATE_PER_S, FRED_BURST)
    adapter = GuardedAdapter(
        {BLS_API_HOST: bls, FRED_API_HOST: fred},
        inner=inner or make_inner_adapter(HTTP_RETRIES),
        timeout_s=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
    )
    return Reliability(
        bls=bls,
        fred=fred,
        sessions=SessionFactory(adapter, user_agent),
        bls_quota_reserve=(
            BLS_QUOTA_RESERVE_REGISTERED if has_bls_key else BLS_QUOTA_RESERVE_ANONYMOUS
        ),
    )
