"""Free-text resolution: catalog retrieval (BM25) and occupation/area matching.

Copied from Project 1 (``labor_agent.resolve``, commit bdfb586) so the gateway is self-contained
(DESIGN.md decision 54). Project 1's LLM-based resolver was not copied: the gateway has no LLM.
"""

from econ_gateway.resolve.entities import match_areas, match_occupations
from econ_gateway.resolve.retrieval import CatalogRetriever
from econ_gateway.resolve.text import expand, tokenize

__all__ = ["CatalogRetriever", "expand", "match_areas", "match_occupations", "tokenize"]
