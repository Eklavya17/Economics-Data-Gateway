"""Toolsets: groups of tools mounted on the main server and selected with ``ECON_TOOLSETS``.

Enabling only the toolsets a client needs keeps its tool list short, which helps the model
choose the right tool and saves context (docs/RESEARCH.md §8).

=========  ==========================================================================
core       search_series, describe_series, get_latest, get_series, find_area
wages      find_occupation, get_occupation_wages        (needs core for find_area)
regional   get_area_employment                          (needs core for find_area)
compute    real_wage_growth, compare_series             (needs core for search_series)
=========  ==========================================================================
"""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from econ_gateway.config import Toolset
from econ_gateway.services.upstream import Upstream
from econ_gateway.toolsets import compute, core, regional, wages

TOOLSET_BUILDERS: dict[Toolset, Callable[[Upstream], FastMCP]] = {
    "core": core.build,
    "wages": wages.build,
    "regional": regional.build,
    "compute": compute.build,
}

__all__ = ["TOOLSET_BUILDERS"]
