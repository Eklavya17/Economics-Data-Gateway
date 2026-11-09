"""Candidate retrieval over the series catalog.

BM25 over title, industry, measure and keywords with synonym expansion, plus rules:
- geography: a series tied to a state, metro or region only ranks high if the query names it;
- demographic breakdowns are demoted unless the query mentions the group;
- industry-specific series are demoted unless the query names a matching industry;
- unvalidated ("pending") series are slightly demoted.

Origin: Project 1 (``labor_agent.resolve.retrieval``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import pandas as pd

from econ_gateway.resolve.text import expand, tokenize

NATIONAL = "United States"
AGGREGATE_INDUSTRIES = {
    "", "total nonfarm", "total private", "nonfarm business", "civilian all industries",
    "private all industries",
}  # fmt: skip


def _place_name(geography: str) -> str:
    return geography.replace(" region", "").lower()


class CatalogRetriever:
    """BM25 ranking of catalog rows for a free-text query.

    Args:
        catalog: The curated catalog (``sources.load_catalog``).
        k1: BM25 term-frequency saturation.
        b: BM25 length normalization.
    """

    def __init__(self, catalog: pd.DataFrame, k1: float = 1.4, b: float = 0.6):
        self.cat = catalog.reset_index(drop=True)
        self.k1, self.b = k1, b
        self.docs = [self._doc_tokens(row) for row in self.cat.itertuples()]
        self.doc_tf = [Counter(doc) for doc in self.docs]
        self.avgdl = sum(len(doc) for doc in self.docs) / max(len(self.docs), 1)
        doc_freq = Counter(token for doc in self.docs for token in set(doc))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in doc_freq.items()}
        geographies = {g for g in self.cat["geography"].unique() if g and g != NATIONAL}
        self.geo_patterns = {
            g: re.compile(r"\b" + re.escape(_place_name(g)) + r"\b") for g in geographies
        }

    @staticmethod
    def _doc_tokens(row: Any) -> list[str]:
        """Searchable text of a row; title and industry count twice."""
        measure = str(row.measure).replace("_", " ")
        text = " ".join(
            [
                row.title, row.title, str(row.industry), str(row.industry), measure,
                str(row.demographic), str(row.keywords), str(row.category),
            ]
        )  # fmt: skip
        return tokenize(text)

    def detect_geographies(self, query: str) -> set[str]:
        """Catalog geographies named in ``query``; the longest match wins.

        "West Virginia" beats both "Virginia" and the "West" region.
        """
        q = query.lower()
        found = {g for g, pattern in self.geo_patterns.items() if pattern.search(q)}
        return {
            g
            for g in found
            if not any(
                _place_name(g) != _place_name(o) and _place_name(g) in _place_name(o) for o in found
            )
        }

    def _bm25(self, q_tokens: list[str], i: int) -> float:
        tf, dl = self.doc_tf[i], len(self.docs[i])
        score = 0.0
        for token in set(q_tokens):
            if token not in tf:
                continue
            f = tf[token]
            norm = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += self.idf.get(token, 0.0) * f * (self.k1 + 1) / norm
        return score

    def _adjust(self, score: float, row: Any, geos: set[str], q_set: set[str]) -> float:
        """Apply the geography, demographic, industry and quality rules to a BM25 score."""
        geography = row.geography or NATIONAL
        if geos:
            score *= 2.0 if geography in geos else (0.7 if geography == NATIONAL else 0.1)
        elif geography != NATIONAL:
            score *= 0.25
        if row.demographic:
            score *= 1.3 if set(tokenize(row.demographic)) & q_set else 0.4
        # Industry-specific series only rank high when the query names a matching industry.
        if row.industry.lower() not in AGGREGATE_INDUSTRIES and not (
            set(expand(tokenize(row.industry))) & q_set
        ):
            score *= 0.6
        title = row.title.lower()
        if "all items" in title and "less" not in title:
            score *= 1.2  # the headline index, unless a component is asked for
        if row.validated == "pending":
            score *= 0.9
        if row.seasonal_adjustment == "SA":
            score *= 1.03
        return score

    def search(self, query: str, k: int = 10, geography: str | None = None) -> pd.DataFrame:
        """Top ``k`` catalog rows for ``query`` with a ``score`` column (empty if none match).

        Args:
            query: Free text, e.g. "unemployment rate Texas".
            k: Maximum rows.
            geography: Extra place name to consider alongside the query.
        """
        q_tokens = expand(tokenize(query))
        if not q_tokens:
            return self.cat.head(0).assign(score=[])
        geos = self.detect_geographies(f"{query} {geography or ''}")
        q_set = set(q_tokens)
        scores = []
        for i, row in enumerate(self.cat.itertuples()):
            score = self._bm25(q_tokens, i)
            scores.append(self._adjust(score, row, geos, q_set) if score > 0 else 0.0)
        out = self.cat.assign(score=scores)
        out = out.loc[out["score"] > 0].sort_values(by="score", ascending=False)
        return out.head(k).reset_index(drop=True)
