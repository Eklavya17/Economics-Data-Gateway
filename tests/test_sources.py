"""Data-source clients and resolvers (``econ_gateway.sources`` / ``econ_gateway.resolve``).

Ported from Project 1's tests (``tests/test_bls.py``, ``test_catalog_cache.py``,
``test_fred_qcew_oews.py`` at commit bdfb586) along with the code, plus resolver checks. HTTP is
faked with a recording session; nothing touches the network.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from econ_gateway.resolve import CatalogRetriever, match_areas, match_occupations
from econ_gateway.sources import DiskCache, infer_source, load_catalog
from econ_gateway.sources.bls import (
    BLSClient,
    BLSError,
    parse_bls_response,
    parse_period,
    year_windows,
)
from econ_gateway.sources.fred import FREDClient, FREDError, parse_observations
from econ_gateway.sources.oews import clean_oews
from econ_gateway.sources.qcew import fetch_qcew, parse_qcew_csv

# ---------------------------------------------------------------- fakes


class FakeResponse:
    """The parts of ``requests.Response`` the clients use."""

    def __init__(self, payload: Any = None, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records calls and returns queued responses."""

    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("GET", url, kwargs)


PRELIMINARY = [{"code": "P", "text": "preliminary"}]


def bls_payload(
    series: dict[str, list[tuple[str, str, str]]],
    status: str = "REQUEST_SUCCEEDED",
    message: list[str] | None = None,
) -> dict[str, Any]:
    """A BLS v2 payload; ``series`` maps ID -> [(year, period, value)]; M12 is preliminary."""
    return {
        "status": status,
        "message": message or [],
        "Results": {
            "series": [
                {
                    "seriesID": sid,
                    "data": [
                        {
                            "year": y, "period": p, "periodName": "", "value": v,
                            "footnotes": PRELIMINARY if p == "M12" else [{}],
                        }
                        for y, p, v in obs
                    ],
                }
                for sid, obs in series.items()
            ]
        },
    }  # fmt: skip


@pytest.fixture
def cache(tmp_path) -> DiskCache:
    return DiskCache(tmp_path / "cache", ttl_hours=12)


# ---------------------------------------------------------------- BLS


@pytest.mark.parametrize(
    ("period", "expected_month", "freq"),
    [("M01", 1, "M"), ("M12", 12, "M"), ("M13", 1, "A"), ("Q01", 1, "Q"), ("Q03", 7, "Q"),
     ("Q05", 1, "A"), ("S01", 1, "S"), ("S02", 7, "S"), ("S03", 1, "A"), ("A01", 1, "A")],
)  # fmt: skip
def test_parse_period(period, expected_month, freq):
    day, code = parse_period(2024, period)
    assert day == datetime(2024, expected_month, 1)
    assert code == freq


def test_parse_period_unknown():
    assert parse_period(2024, "X01") == (None, "?")


def test_parse_response_values_and_footnotes():
    payload = bls_payload({"CES3000000003": [("2024", "M12", "35.10"), ("2024", "M11", "-")]})
    frame, _, messages = parse_bls_response(payload)
    assert list(frame["period"]) == ["M11", "M12"]  # sorted by date
    assert math.isnan(frame.loc[0, "value"])  # type: ignore[arg-type]
    assert frame.loc[1, "value"] == 35.10
    assert frame.loc[1, "footnotes"] == "preliminary"
    assert messages == []


def test_parse_response_failure_raises():
    payload = {"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold reached"]}
    with pytest.raises(BLSError, match="daily threshold"):
        parse_bls_response(payload)


def test_year_windows():
    assert year_windows(2000, 2024, 10) == [(2000, 2009), (2010, 2019), (2020, 2024)]
    assert year_windows(2020, 2020, 20) == [(2020, 2020)]


def test_fetch_batches_unregistered_and_caches(cache):
    # 30 series, 12 years, no key -> 25-series/10-year limits -> 2 groups x 2 windows = 4 calls
    ids = [f"CES{n:010d}" for n in range(30)]
    responses = [
        FakeResponse(bls_payload({s: [(str(start), "M01", "1.0")] for s in group}))
        for group in (ids[:25], ids[25:])
        for start in (2013, 2023)
    ]
    session = FakeSession(responses)
    client = BLSClient(api_key="", session=session, cache=cache)  # type: ignore[arg-type]
    frame = client.fetch(ids, 2013, 2024)
    assert client.queries_made == 4
    assert len(session.calls[0][2]["json"]["seriesid"]) == 25
    assert "registrationkey" not in session.calls[0][2]["json"]
    assert pd.Series(frame["series_id"]).nunique() == 30
    assert len(frame) == 60
    # The second call is served from the cache: no HTTP at all.
    again = BLSClient(api_key="", session=FakeSession([]), cache=cache).fetch(ids, 2013, 2024)  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(
        frame.reset_index(drop=True), again.reset_index(drop=True), check_dtype=False
    )


def test_fetch_registered_sends_key_and_uses_larger_limits(cache):
    payload = bls_payload({"CUUR0000SA0": [("2010", "M01", "216.687")]})
    session = FakeSession([FakeResponse(payload)])
    client = BLSClient(api_key="KEY123", session=session, cache=cache)  # type: ignore[arg-type]
    frame = client.fetch("cuur0000sa0", 2005, 2024)  # 20 years: one window with a key
    body = session.calls[0][2]["json"]
    assert body["registrationkey"] == "KEY123" and body["seriesid"] == ["CUUR0000SA0"]
    assert client.queries_made == 1
    assert frame.loc[0, "value"] == pytest.approx(216.687)


def test_fetch_empty_series_not_cached(cache):
    payload = bls_payload({"BADID": []}, message=["Series does not exist for Series BADID"])
    client = BLSClient(api_key="", session=FakeSession([FakeResponse(payload)]), cache=cache)  # type: ignore[arg-type]
    assert client.fetch("BADID", 2023, 2024).empty
    assert "Series does not exist" in client.last_messages[0]
    parts = {"series_id": "BADID", "start": 2023, "end": 2024, "annual_average": False}
    assert cache.get("bls", parts) is None


def test_start_after_end_raises(cache):
    with pytest.raises(ValueError):
        BLSClient(api_key="", session=FakeSession([]), cache=cache).fetch("X", 2025, 2020)  # type: ignore[arg-type]


# ---------------------------------------------------------------- FRED, QCEW, OEWS


def test_parse_fred_observations_missing_dot():
    payload = {"observations": [{"date": "2024-01-01", "value": "3.7"},
                                {"date": "2024-02-01", "value": "."}]}  # fmt: skip
    frame = parse_observations("UNRATE", payload)
    assert list(frame.columns) == ["series_id", "date", "value", "source"]
    assert frame.loc[0, "value"] == 3.7
    assert math.isnan(frame.loc[1, "value"])  # type: ignore[arg-type]
    assert frame.loc[1, "date"] == pd.Timestamp("2024-02-01")


def test_fred_requires_key(cache):
    with pytest.raises(FREDError, match="FRED_API_KEY"):
        FREDClient(api_key="", session=FakeSession([]), cache=cache).observations("UNRATE")  # type: ignore[arg-type]


def test_fred_error_message_surfaces_without_the_key(cache):
    body = {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
    session = FakeSession([FakeResponse(body, status_code=400)])
    client = FREDClient(api_key="SECRETKEY", session=session, cache=cache)  # type: ignore[arg-type]
    with pytest.raises(FREDError, match="does not exist") as err:
        client.observations("NOPE")
    assert "SECRETKEY" not in str(err.value)


def test_fred_observations_params_and_cache(cache):
    session = FakeSession(
        [FakeResponse({"observations": [{"date": "2024-01-01", "value": "310.3"}]})]
    )
    client = FREDClient(api_key="k", session=session, cache=cache)  # type: ignore[arg-type]
    frame = client.observations("cpiaucsl", "2024-01-01", units="pc1")
    params = session.calls[0][2]["params"]
    assert params["series_id"] == "CPIAUCSL" and params["units"] == "pc1"
    assert "observation_end" not in params  # None parameters are dropped
    cached = FREDClient(api_key="k", session=FakeSession([]), cache=cache)  # type: ignore[arg-type]
    again = cached.observations("CPIAUCSL", "2024-01-01", units="pc1")
    assert again.loc[0, "value"] == frame.loc[0, "value"]


def test_parse_qcew_keeps_codes_as_strings():
    csv = (
        "area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,avg_wkly_wage\n"
        '"01001","0","10","70","0",2024,1,1000\n'
    )
    frame = parse_qcew_csv(csv)
    assert frame.loc[0, "area_fips"] == "01001"  # leading zero preserved
    assert frame.loc[0, "avg_wkly_wage"] == 1000


def test_qcew_argument_validation(cache):
    with pytest.raises(ValueError):
        fetch_qcew(2024, 1, cache=cache)
    with pytest.raises(ValueError):
        fetch_qcew(2024, 5, area="US000", cache=cache)


def test_clean_oews_flags_top_coded_and_missing_values():
    raw = pd.DataFrame(
        {"AREA": ["12420"], "OCC_CODE": ["29-1141"], "TOT_EMP": ["12,340"], "A_MEAN": ["#"],
         "H_MEAN": ["*"], "A_MEDIAN": ["95,000"], "ANNUAL": [None], "HOURLY": ["TRUE"]}
    )  # fmt: skip
    frame = clean_oews(raw)
    assert frame.loc[0, "tot_emp"] == 12340
    assert math.isnan(frame.loc[0, "a_mean"]) and bool(frame.loc[0, "a_mean_capped"])  # type: ignore[arg-type]
    assert math.isnan(frame.loc[0, "h_mean"]) and not bool(frame.loc[0, "h_mean_capped"])  # type: ignore[arg-type]
    assert frame.loc[0, "a_median"] == 95000
    assert bool(frame.loc[0, "hourly"]) and not bool(frame.loc[0, "annual"])


# ---------------------------------------------------------------- cache and catalog


def test_cache_roundtrip_ttl_and_fetched_at(tmp_path):
    frame = pd.DataFrame({"a": [1, 2]})
    cache = DiskCache(tmp_path, ttl_hours=12)
    cache.put("ns", {"k": 1}, frame)
    assert cache.get("ns", {"k": 1}).equals(frame)  # type: ignore[union-attr]
    assert cache.get("ns", {"k": 2}) is None
    assert cache.get("ns", {"k": 1}, ttl_hours=0) is None  # expired
    assert cache.get("ns", {"k": 1}, ttl_hours=math.inf) is not None
    assert cache.fetched_at("ns", {"k": 1}) is not None
    assert cache.clear("ns") == 2


def test_shipped_catalog_loads_and_is_unique():
    catalog = load_catalog()
    assert len(catalog) >= 250
    assert set(catalog["source"]) <= {"BLS", "FRED"}
    assert catalog["series_id"].is_unique


def test_infer_source():
    assert infer_source("CES3000000003") == "BLS"
    assert infer_source("UNRATE") == "FRED"
    assert infer_source("CUUR0000SA0") == "BLS"
    assert infer_source("LNS14000000") == "BLS"
    assert infer_source("GDPC1") == "FRED"


# ---------------------------------------------------------------- resolvers


def test_catalog_search_ranks_relevant_series():
    hits = CatalogRetriever(load_catalog()).search("manufacturing wages")
    assert not hits.empty and str(hits.iloc[0]["series_id"]).startswith("CES30")


def test_catalog_search_prefers_the_named_state():
    hits = CatalogRetriever(load_catalog()).search("unemployment rate Texas", k=3)
    assert hits.iloc[0]["geography"] == "Texas"


def test_occupation_aliases_and_codes():
    assert match_occupations("registered nurse").iloc[0]["occ_code"] == "29-1141"
    assert match_occupations("15-1252").iloc[0]["occ_code"] == "15-1252"


def test_state_abbreviation_matches_the_state():
    best = match_areas("TX", level="state").iloc[0]
    assert best["area_title"] == "Texas"
