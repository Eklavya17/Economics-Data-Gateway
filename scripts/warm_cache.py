"""Download and parse the latest OEWS tables ahead of time.

The first OEWS request for a new year downloads BLS zip files (tens of MB) and parses Excel,
which can take longer than a tool call should. Run this once after each spring OEWS release:

    .venv/Scripts/python.exe scripts/warm_cache.py
"""

from __future__ import annotations

import time
from datetime import date

from econ_gateway.services.upstream import LiveUpstream
from econ_gateway.services.upstream_places import OewsLevel
from econ_gateway.services.wages import latest_oews_year

LEVELS: tuple[OewsLevel, ...] = ("national", "state", "metro")


def main() -> None:
    """Load the newest OEWS year at every level and report timings."""
    upstream = LiveUpstream()
    year = latest_oews_year(upstream, date.today())
    print(f"latest OEWS release: May {year}")
    for level in LEVELS:
        started = time.perf_counter()
        rows = len(upstream.oews(year, level))
        print(f"  {level:<8} {rows:>7} rows  {time.perf_counter() - started:5.1f}s")


if __name__ == "__main__":
    main()
