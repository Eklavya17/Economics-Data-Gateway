import pytest

from econ_gateway.services.errors import SeriesNotFoundError, UpstreamError
from econ_gateway.services.metadata import clean_notes, is_rate, meta_from_bls
from econ_gateway.services.upstream import LiveUpstream
from econ_gateway.sources import FREDError


@pytest.mark.parametrize(
    "message",
    [
        "FRED series failed (400): Bad Request.  The series does not exist.",
        # Real FRED reply for a malformed ID such as NOT_A_SERIES (seen in a live run).
        "FRED series/observations failed (400): Bad Request.  Invalid value for variable "
        "series_id.  Series IDs should be 25 or less alphanumeric characters.",
    ],
)
def test_fred_unknown_or_malformed_ids_become_not_found(message):
    err = LiveUpstream._translate_fred_error("XYZ", FREDError(message))
    assert isinstance(err, SeriesNotFoundError)
    assert "search_series" in str(err)


def test_other_fred_failures_are_upstream_errors():
    err = LiveUpstream._translate_fred_error("UNRATE", FREDError("failed (500): oops"))
    assert isinstance(err, UpstreamError)


def test_clean_notes_normalizes_windows_line_endings():
    assert clean_notes("First.\r\n\r\n\r\nSecond. \r\n") == "First.\n\nSecond."
    assert clean_notes("") is None


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        ("percent", True),
        ("Percent", True),
        ("percentage points", True),
        ("unemployment rate", True),  # BLS metadata wording for LAUS (seen live)
        ("index 1982-84=100", False),
        ("dollars per hour", False),
        ("thousands of jobs", False),
    ],
)
def test_is_rate(units, expected):
    assert is_rate(units) is expected


def test_bls_metadata_rates_are_reported_in_percent():
    meta = meta_from_bls("LAUMT481242000000003", {"measure_data_type": "unemployment rate"})
    assert meta.units == "percent"
