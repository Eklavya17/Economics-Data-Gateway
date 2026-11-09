"""Data-source clients: BLS, FRED, OEWS and QCEW, a disk cache, and the curated catalog.

This package was copied from Project 1 (the Labor Market Intelligence Agent, ``labor_agent.data``
at commit bdfb586) so the gateway runs on its own: one repository, one install, one Docker build
(DESIGN.md decision 54). It is now maintained here; changes are no longer shared with Project 1.

Layers: ``toolsets`` → ``services`` → ``sources``. Services reach the network only through
``services/upstream.py``, which hands these clients a guarded session and a release-aware cache.
"""

from econ_gateway.sources.bls import BLSClient, BLSError
from econ_gateway.sources.cache import DiskCache
from econ_gateway.sources.catalog import (
    load_catalog,
    load_occupations,
    load_oews_areas,
    load_qcew_areas,
)
from econ_gateway.sources.fred import FREDClient, FREDError
from econ_gateway.sources.oews import OEWSError, load_oews
from econ_gateway.sources.qcew import QCEWError, fetch_qcew
from econ_gateway.sources.series import get_series, infer_source
from econ_gateway.sources.settings import SourceSettings, get_source_settings

__all__ = [
    "BLSClient",
    "BLSError",
    "DiskCache",
    "FREDClient",
    "FREDError",
    "OEWSError",
    "QCEWError",
    "SourceSettings",
    "fetch_qcew",
    "get_series",
    "get_source_settings",
    "infer_source",
    "load_catalog",
    "load_occupations",
    "load_oews",
    "load_oews_areas",
    "load_qcew_areas",
]
