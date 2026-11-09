"""Business logic. No MCP imports here, so everything is testable and reusable without a server."""

from econ_gateway.services.areas import find_area
from econ_gateway.services.compare import compare_series
from econ_gateway.services.describe import describe_series
from econ_gateway.services.employment import get_area_employment
from econ_gateway.services.observations import get_latest, get_series
from econ_gateway.services.real_wages import real_wage_growth
from econ_gateway.services.search import search_series
from econ_gateway.services.upstream import LiveUpstream, Upstream
from econ_gateway.services.wages import find_occupation, get_occupation_wages

__all__ = [
    "LiveUpstream",
    "Upstream",
    "compare_series",
    "describe_series",
    "find_area",
    "find_occupation",
    "get_area_employment",
    "get_latest",
    "get_occupation_wages",
    "get_series",
    "real_wage_growth",
    "search_series",
]
