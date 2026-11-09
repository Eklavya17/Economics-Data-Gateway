"""Default ``requests`` session with retries, for clients used without the gateway's guards.

The gateway's own BLS/FRED calls go through the guarded session (``infra/http.py``). This plain
session is used for OEWS and QCEW file downloads, and when a client is created without a
session (tests, scripts, eval oracles).

Origin: Project 1 (``labor_agent.data.http``, commit bdfb586).
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from econ_gateway.sources.settings import get_source_settings

DEFAULT_TIMEOUT = 60  # seconds per request; the gateway's guarded session caps this lower


def make_session(user_agent: str | None = None, retries: int = 3) -> requests.Session:
    """A session that retries connection errors, 429 and 5xx with exponential backoff."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = user_agent or get_source_settings().bls_user_agent
    return session
