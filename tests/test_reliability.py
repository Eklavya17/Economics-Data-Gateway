"""Unit tests for the Phase 4 building blocks: token bucket, breaker, single-flight, quota,
guarded HTTP adapter, release-aware cache and the release-calendar freshness rule."""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests
from requests.adapters import BaseAdapter
from requests.models import PreparedRequest, Response

from econ_gateway.consts import CACHE_DEFAULT_TTL_S, CACHE_MAX_AGE_S, RELEASE_SETTLE_RECHECK_S
from econ_gateway.infra.breaker import CircuitBreaker
from econ_gateway.infra.cache import ReleaseAwareCache
from econ_gateway.infra.errors import (
    CircuitOpenError,
    DisallowedHostError,
    QuotaExhaustedError,
    RateLimitedError,
)
from econ_gateway.infra.http import Cert, GuardedAdapter, Timeout, UpstreamGuard
from econ_gateway.infra.quota import DailyQuota
from econ_gateway.infra.rate_limit import TokenBucket
from econ_gateway.infra.single_flight import SingleFlight
from econ_gateway.services.release_calendar import (
    ReleaseCalendar,
    bls_release_id,
    release_instant,
)

ET = ZoneInfo("America/New_York")


class FakeClock:
    """Manually advanced clock; ``sleep`` advances it instead of blocking."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------- token bucket


def test_bucket_allows_burst_then_paces():
    clock = FakeClock()
    bucket = TokenBucket(2.0, 3, clock=clock, sleep=clock.sleep)
    assert all(bucket.acquire(0) for _ in range(3))  # the burst
    assert not bucket.acquire(0.1)  # next token is 0.5 s away
    started = clock.now
    assert bucket.acquire(5)
    assert clock.now - started == pytest.approx(0.5)


def test_bucket_refills_to_capacity_only():
    clock = FakeClock()
    bucket = TokenBucket(1.0, 2, clock=clock, sleep=clock.sleep)
    bucket.acquire(0)
    clock.now += 100
    assert bucket.available == 2


# ---------------------------------------------------------------- circuit breaker


def _breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker("BLS", failure_threshold=3, reset_timeout_s=60, clock=clock)


def test_breaker_opens_after_threshold_and_fails_fast():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.before_call()
        breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError) as err:
        breaker.before_call()
    assert err.value.retry_after_s == pytest.approx(60)
    assert "Retry in about 60 s" in err.value.advice


def test_breaker_success_resets_failure_count():
    breaker = _breaker(FakeClock())
    for _ in range(2):
        breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == "closed"


def test_breaker_half_open_allows_one_probe():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 61
    assert breaker.state == "half_open"
    breaker.before_call()  # the probe
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # a second caller while the probe runs
    breaker.record_success()
    assert breaker.state == "closed"
    breaker.before_call()


def test_breaker_failed_probe_reopens():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 61
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.snapshot()["retry_after_s"] == pytest.approx(60)


# ---------------------------------------------------------------- single flight


def test_single_flight_merges_concurrent_calls():
    flights = SingleFlight()
    entered, release = threading.Event(), threading.Event()
    calls: list[int] = []
    results: list[tuple[str, bool]] = []

    def work() -> str:
        calls.append(1)
        entered.set()
        release.wait(5)
        return "data"

    leader = threading.Thread(target=lambda: results.append(flights.do("k", work)))
    leader.start()
    entered.wait(5)
    follower = threading.Thread(target=lambda: results.append(flights.do("k", work)))
    follower.start()
    time.sleep(0.05)  # let the follower reach the wait
    release.set()
    leader.join(5)
    follower.join(5)
    assert len(calls) == 1
    assert sorted(results) == [("data", False), ("data", True)]
    assert flights.in_flight == 0


def test_single_flight_shares_exceptions_and_forgets_key():
    flights = SingleFlight()

    def boom() -> str:
        raise ValueError("upstream down")

    with pytest.raises(ValueError):
        flights.do("k", boom)
    assert flights.do("k", lambda: "ok") == ("ok", False)


# ---------------------------------------------------------------- daily quota


def _et_epoch(year: int, month: int, day: int, hour: int = 12) -> float:
    return datetime(year, month, day, hour, tzinfo=ET).timestamp()


def test_quota_counts_reserves_and_raises(tmp_path: Path):
    clock = FakeClock(_et_epoch(2026, 11, 9))
    quota = DailyQuota("BLS", limit=5, path=tmp_path / "q.json", tz=ET, clock=clock)
    quota.consume(3)
    quota.check()  # 2 left
    with pytest.raises(QuotaExhaustedError):
        quota.check(reserve=2)  # metadata calls must leave 2 for data
    quota.consume(2)
    with pytest.raises(QuotaExhaustedError) as err:
        quota.check()
    assert "resets at midnight US Eastern, in about 12 h" in err.value.advice


def test_quota_persists_and_rolls_over_at_eastern_midnight(tmp_path: Path):
    clock = FakeClock(_et_epoch(2026, 11, 9, hour=23))
    path = tmp_path / "q.json"
    DailyQuota("BLS", limit=500, path=path, tz=ET, clock=clock).consume(7)
    restarted = DailyQuota("BLS", limit=500, path=path, tz=ET, clock=clock)
    assert restarted.remaining() == 493
    clock.now += 2 * 3600  # 01:00 ET the next day
    assert restarted.remaining() == 500


def test_quota_trusts_upstream_exhaustion(tmp_path: Path):
    quota = DailyQuota("BLS", limit=500, path=tmp_path / "q.json", tz=ET, clock=FakeClock())
    quota.mark_exhausted()
    assert quota.remaining() == 0


def test_quota_survives_corrupt_file(tmp_path: Path):
    path = tmp_path / "q.json"
    path.write_text("{not json")
    assert DailyQuota("BLS", limit=25, path=path, tz=ET).remaining() == 25


# ---------------------------------------------------------------- guarded adapter


class FakeTransport(BaseAdapter):
    """Inner adapter that records requests and answers with a fixed status."""

    def __init__(self, status: int = 200):
        super().__init__()
        self.status = status
        self.timeouts: list[Timeout] = []

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: Cert = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        self.timeouts.append(timeout)
        response = Response()
        response.status_code = self.status
        response.request = request
        response.url = request.url or ""
        return response

    def close(self) -> None:
        pass


def _guarded_session(
    transport: FakeTransport, *, burst: int = 10, quota: DailyQuota | None = None
) -> tuple[requests.Session, UpstreamGuard]:
    clock = FakeClock()
    guard = UpstreamGuard(
        name="BLS",
        bucket=TokenBucket(1.0, burst, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker("BLS", failure_threshold=3, reset_timeout_s=60, clock=clock),
        max_wait_s=0,
        quota=quota,
    )
    adapter = GuardedAdapter({"api.bls.gov": guard}, inner=transport, timeout_s=(5, 30))
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, guard


@pytest.mark.parametrize(
    "url", ["https://evil.example.com/x", "http://api.bls.gov/publicAPI/v2/timeseries/data/"]
)
def test_adapter_refuses_hosts_outside_allow_list_and_plain_http(url: str):
    transport = FakeTransport()
    session, _ = _guarded_session(transport)
    with pytest.raises(DisallowedHostError):
        session.get(url)
    assert transport.timeouts == []


def test_adapter_caps_timeouts():
    transport = FakeTransport()
    session, _ = _guarded_session(transport)
    session.get("https://api.bls.gov/x", timeout=60)  # the clients ask for 60 s
    session.get("https://api.bls.gov/x")
    assert transport.timeouts == [(5, 30), (5, 30)]


def test_adapter_opens_breaker_on_5xx_and_then_fails_fast():
    transport = FakeTransport(status=503)
    session, guard = _guarded_session(transport)
    for _ in range(3):
        assert session.get("https://api.bls.gov/x").status_code == 503
    with pytest.raises(CircuitOpenError):
        session.get("https://api.bls.gov/x")
    assert len(transport.timeouts) == 3
    assert guard.snapshot()["circuit"]["state"] == "open"  # type: ignore[index]


def test_adapter_treats_4xx_as_healthy():
    transport = FakeTransport(status=400)
    session, guard = _guarded_session(transport)
    for _ in range(5):
        session.get("https://api.bls.gov/x")
    assert guard.breaker.state == "closed"


def test_adapter_rate_limits_without_waiting_past_budget():
    session, _ = _guarded_session(FakeTransport(), burst=2)
    session.get("https://api.bls.gov/x")
    session.get("https://api.bls.gov/x")
    with pytest.raises(RateLimitedError):
        session.get("https://api.bls.gov/x")


def test_adapter_spends_and_enforces_quota(tmp_path: Path):
    quota = DailyQuota("BLS", limit=2, path=None, tz=ET)
    transport = FakeTransport()
    session, _ = _guarded_session(transport, quota=quota)
    session.post("https://api.bls.gov/x")
    session.post("https://api.bls.gov/x")
    with pytest.raises(QuotaExhaustedError):
        session.post("https://api.bls.gov/x")
    assert len(transport.timeouts) == 2


# ---------------------------------------------------------------- release-aware cache

PARTS = {"series_id": "CES0500000003", "start": 2025, "end": 2026, "annual_average": False}
FRAME = pd.DataFrame({"series_id": ["CES0500000003"], "value": [36.5]})


def _age_entry(cache: ReleaseAwareCache, seconds: float) -> float:
    """Pretend the entry was stored ``seconds`` ago; returns the new fetched_at."""
    _, meta_path = cache.paths("bls", PARTS)
    meta = json.loads(meta_path.read_text())
    meta["fetched_at"] -= seconds
    meta_path.write_text(json.dumps(meta))
    return meta["fetched_at"]


def test_cache_asks_freshness_check(tmp_path: Path):
    verdict = {"fresh": True}
    cache = ReleaseAwareCache(tmp_path, 12, is_fresh=lambda ns, sid, at: verdict["fresh"])
    cache.put("bls", PARTS, FRAME)
    assert cache.get("bls", PARTS) is not None
    verdict["fresh"] = False
    assert cache.get("bls", PARTS) is None


def test_cache_stale_mode_serves_any_age_and_records_it(tmp_path: Path):
    ReleaseAwareCache(tmp_path, 12).put("bls", PARTS, FRAME)
    stale = ReleaseAwareCache(tmp_path, 12, stale_ok=True)
    fetched_at = _age_entry(stale, 30 * 86400)
    assert stale.get("bls", PARTS) is not None
    assert stale.served == {"CES0500000003": pytest.approx(fetched_at)}


def test_cache_falls_back_to_fixed_ttl_when_check_fails(tmp_path: Path):
    def broken(namespace: str, series_id: str, fetched_at: float) -> bool:
        raise RuntimeError("calendar exploded")

    cache = ReleaseAwareCache(tmp_path, 12, is_fresh=broken)
    cache.put("bls", PARTS, FRAME)
    assert cache.get("bls", PARTS) is not None
    _age_entry(cache, 13 * 3600)
    assert cache.get("bls", PARTS) is None


def test_cache_respects_explicit_ttl_for_non_series_entries(tmp_path: Path):
    cache = ReleaseAwareCache(tmp_path, 12, is_fresh=lambda ns, sid, at: False)
    parts = {"year": 2026, "quarter": 1, "area": "48453"}
    cache.put("qcew", parts, FRAME)
    assert cache.get("qcew", parts, ttl_hours=720) is not None


# ---------------------------------------------------------------- release calendar

JOBS_DAY = date(2026, 11, 6)  # Employment Situation, 8:30 a.m. ET


def _calendar(clock: FakeClock, dates: list[date] | None = None, *, fail: bool = False):
    calls: list[int] = []

    def load(release_id: int, start: date, end: date) -> list[date]:
        calls.append(release_id)
        if fail:
            raise requests.ConnectionError("FRED down")
        return dates if dates is not None else [date(2026, 10, 2), JOBS_DAY, date(2026, 12, 4)]

    calendar = ReleaseCalendar(load, lambda sid: 50 if sid == "PAYEMS" else None, clock=clock)
    return calendar, calls


def test_bls_prefix_table():
    assert bls_release_id("CES0500000003") == 50
    assert bls_release_id("LASST480000000000003") == 112
    assert bls_release_id("LAUMT481242000000003") == 113
    assert bls_release_id("CUSR0000SA0") == 10
    assert bls_release_id("XX123") is None


def test_entry_fetched_before_latest_release_is_stale():
    release = release_instant(JOBS_DAY)
    clock = FakeClock(release + 6 * 3600)
    calendar, _ = _calendar(clock)
    assert not calendar.is_fresh("bls", "CES0500000003", release - 3600)


def test_entry_fetched_after_settle_window_lasts_until_next_release():
    release = release_instant(JOBS_DAY)
    clock = FakeClock(release + 5 * 86400)  # Nov 11: before the Dec 4 release, under max age
    calendar, _ = _calendar(clock)
    assert calendar.is_fresh("bls", "CES0500000003", release + 4 * 3600)
    assert calendar.next_release(50) == date(2026, 12, 4)


def test_entry_fetched_during_settle_window_is_rechecked_soon():
    release = release_instant(JOBS_DAY)
    fetched_at = release + 600  # FRED may not have posted yet
    clock = FakeClock(fetched_at + RELEASE_SETTLE_RECHECK_S - 1)
    calendar, _ = _calendar(clock)
    assert calendar.is_fresh("fred", "PAYEMS", fetched_at)
    clock.now += 2
    assert not calendar.is_fresh("fred", "PAYEMS", fetched_at)


def test_unknown_release_uses_default_ttl_and_max_age_always_applies():
    release = release_instant(JOBS_DAY)
    clock = FakeClock(release + 86400)
    calendar, _ = _calendar(clock)
    assert calendar.is_fresh("fred", "MYSTERY", clock.now - CACHE_DEFAULT_TTL_S + 60)
    assert not calendar.is_fresh("fred", "MYSTERY", clock.now - CACHE_DEFAULT_TTL_S - 60)
    assert not calendar.is_fresh("bls", "CES0500000003", clock.now - CACHE_MAX_AGE_S)


def test_calendar_failure_falls_back_and_retries_later():
    clock = FakeClock(release_instant(JOBS_DAY) + 86400)
    calendar, calls = _calendar(clock, fail=True)
    fetched_at = clock.now - 3600
    assert calendar.is_fresh("bls", "CES0500000003", fetched_at)  # 1 h < default 12 h TTL
    calendar.is_fresh("bls", "CES0500000003", fetched_at)
    assert calls == [50]  # failure remembered; not retried on every check
    clock.now += timedelta(minutes=11).total_seconds()
    calendar.is_fresh("bls", "CES0500000003", fetched_at)
    assert calls == [50, 50]


def test_calendar_caches_release_dates():
    clock = FakeClock(release_instant(JOBS_DAY) + 86400)
    calendar, calls = _calendar(clock)
    for _ in range(3):
        calendar.is_fresh("bls", "CES0500000003", clock.now - 60)
    assert calls == [50]
