"""End-to-end reliability of ``LiveUpstream`` with the real BLS/FRED clients over a fake API.

The fake replaces only the transport (the adapter under the guarded session), so these tests
exercise the real BLS/FRED clients, the release-aware disk cache, the guards, request merging
and the stale fallback together, without network access.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.models import PreparedRequest, Response

from econ_gateway.infra.http import Cert, Timeout
from econ_gateway.services.errors import UpstreamError
from econ_gateway.services.observations import get_latest, get_series
from econ_gateway.services.reliability import build_reliability
from econ_gateway.services.upstream import STALE_COLUMN, LiveUpstream

WAGE_ID = "CES0500000003"


def _bls_payload(series_ids: list[str]) -> dict[str, object]:
    data = [
        {"year": str(year), "period": f"M{month:02d}", "periodName": "", "value": str(value)}
        for value, (year, month) in enumerate(
            [(y, m) for y in (2024, 2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 9)],
            start=30,
        )
    ]
    series = [{"seriesID": sid, "data": data} for sid in series_ids]
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": series}}


def _fred_observations() -> dict[str, object]:
    return {
        "observations": [
            {"date": "2026-08-01", "value": "159000"},
            {"date": "2026-09-01", "value": "159100"},
        ]
    }


class FakeApi(BaseAdapter):
    """Answers BLS and FRED requests from canned data; can be taken down or made to block."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.bls_refusal: str | None = None
        self.paths: list[str] = []
        self.block: threading.Event | None = None
        self.entered = threading.Event()

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: Timeout = None,
        verify: bool | str = True,
        cert: Cert = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        url = urlsplit(request.url or "")
        self.paths.append(url.path)
        if self.down:
            raise requests.ConnectionError("simulated outage")
        return self._json(request, self._payload(url.hostname or "", url.path, request.body))

    def _payload(self, host: str, path: str, body: object) -> dict[str, object]:
        if host == "api.bls.gov":
            if self.bls_refusal:
                return {"status": "REQUEST_NOT_PROCESSED", "message": [self.bls_refusal]}
            return _bls_payload(json.loads(body)["seriesid"])  # type: ignore[arg-type]
        if path.endswith("/series/observations"):
            if self.block is not None:
                self.entered.set()
                self.block.wait(5)
            return _fred_observations()
        if path.endswith("/release/dates"):
            return {"release_dates": [{"date": "2026-10-02"}]}
        if path.endswith("/series/release"):
            return {"releases": [{"id": 50}]}
        raise AssertionError(f"unexpected request to {host}{path}")

    @staticmethod
    def _json(request: PreparedRequest, payload: dict[str, object]) -> Response:
        response = Response()
        response.status_code = 200
        response.request = request
        response.url = request.url or ""
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        return response

    def close(self) -> None:
        pass

    def count(self, suffix: str) -> int:
        return sum(path.endswith(suffix) for path in self.paths)


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def upstream(tmp_path: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> LiveUpstream:
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")
    monkeypatch.setenv("BLS_API_KEY", "test-bls-key")
    reliability = build_reliability(
        has_bls_key=True, state_dir=tmp_path, user_agent="econ-gateway-tests", inner=api
    )
    return LiveUpstream(reliability=reliability, cache_dir=tmp_path / "cache")


def _age_cache(cache_dir: Path, seconds: float) -> None:
    """Make every cached entry look ``seconds`` older (past the release calendar's max age)."""
    for meta_path in cache_dir.rglob("*.json"):
        meta = json.loads(meta_path.read_text())
        meta["fetched_at"] -= seconds
        meta_path.write_text(json.dumps(meta))


def test_fresh_cache_hit_needs_no_network(upstream: LiveUpstream, api: FakeApi):
    first, _ = upstream.fetch_bls([WAGE_ID], 2025, 2026)
    api.down = True
    second, _ = upstream.fetch_bls([WAGE_ID], 2025, 2026)
    assert api.count("/timeseries/data/") == 1
    assert second["value"].tolist() == first["value"].tolist()
    assert STALE_COLUMN not in second.columns


def test_outage_serves_stale_copy_and_says_so(upstream: LiveUpstream, api: FakeApi, tmp_path: Path):
    today = date(2026, 11, 8)
    get_latest([WAGE_ID], upstream, today=today)  # warm the cache
    _age_cache(tmp_path / "cache", 8 * 86400)
    api.down = True

    response = get_latest([WAGE_ID], upstream, today=today)

    (result,) = response.results
    assert result.latest_period == "2026-09"
    assert result.meta.stale_as_of is not None
    assert any("could not be reached" in note for note in result.notes)

    series = get_series([WAGE_ID], upstream, start_date="2024-01", max_points=3, today=today)
    assert series.results[0].meta.stale_as_of == result.meta.stale_as_of
    assert any("could not be reached" in note for note in series.notes)


def test_outage_without_cache_is_an_actionable_error(upstream: LiveUpstream, api: FakeApi):
    api.down = True
    with pytest.raises(UpstreamError, match="Could not reach the BLS API.*no cached copy"):
        upstream.fetch_bls([WAGE_ID], 2025, 2026)


def test_breaker_stops_calling_a_failing_upstream(upstream: LiveUpstream, api: FakeApi):
    api.down = True
    for _ in range(3):
        with pytest.raises(UpstreamError, match="Could not reach the FRED API"):
            upstream.fetch_fred("PAYEMS", "2026-01-01", "2026-10-01")
    with pytest.raises(UpstreamError, match="pausing requests"):
        upstream.fetch_fred("PAYEMS", "2026-01-01", "2026-10-01")
    assert api.count("/series/observations") == 3
    assert upstream.status()["FRED"]["circuit"]["state"] == "open"  # type: ignore[index]


def test_identical_concurrent_fetches_hit_upstream_once(upstream: LiveUpstream, api: FakeApi):
    api.block = threading.Event()
    frames: list[object] = []

    def fetch() -> None:
        frames.append(upstream.fetch_fred("PAYEMS", "2026-01-01", "2026-10-01"))

    leader = threading.Thread(target=fetch)
    leader.start()
    assert api.entered.wait(5)
    follower = threading.Thread(target=fetch)
    follower.start()
    time.sleep(0.05)  # the follower is now waiting on the leader's request
    api.block.set()
    leader.join(5)
    follower.join(5)
    assert api.count("/series/observations") == 1
    assert len(frames) == 2
    assert frames[0] is not frames[1]  # each caller gets its own copy


def test_bls_threshold_refusal_exhausts_quota(upstream: LiveUpstream, api: FakeApi):
    api.bls_refusal = "The daily threshold for total number of requests allocated is reached."
    with pytest.raises(UpstreamError, match="daily threshold"):
        upstream.fetch_bls([WAGE_ID], 2025, 2026)
    assert upstream.reliability.bls_quota.remaining() == 0

    with pytest.raises(UpstreamError, match="FRED carries copies"):
        upstream.fetch_bls(["CES3000000003"], 2025, 2026)
    assert api.count("/timeseries/data/") == 1  # refused locally, BLS not asked again


def test_metadata_lookups_stop_at_quota_reserve(upstream: LiveUpstream, api: FakeApi):
    quota = upstream.reliability.bls_quota
    quota.consume(quota.limit - upstream.reliability.bls_quota_reserve)
    assert upstream.bls_series_info(WAGE_ID) is None
    assert api.paths == []


def test_quota_count_persists_in_state_dir(upstream: LiveUpstream, tmp_path: Path):
    upstream.fetch_bls([WAGE_ID], 2025, 2026)
    saved = json.loads((tmp_path / "bls_quota.json").read_text())
    assert saved["used"] == 1
