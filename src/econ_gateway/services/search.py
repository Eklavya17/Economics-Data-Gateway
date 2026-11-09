"""Series discovery for ``search_series``.

The curated catalog (~300 validated series, BM25 with economics synonyms) is searched first:
it is free, so it uses no API quota, and every ID in it is known to work. FRED full-text
search (800k+ series, including state and metro copies of BLS series) is added only when the
catalog is likely to miss (DESIGN.md decision 4):

- the catalog has fewer than ``FRED_FALLBACK_MIN_CATALOG_HITS`` matches, or
- a place is named that none of the catalog matches cover (e.g. "average hourly earnings
  Texas" while the catalog only has the national series).

FRED's own orderings are poor for this use (DESIGN.md decision 8): by popularity, "earnings:
Construction in Texas" ties with "earnings: Total Private in Texas". FRED matches are therefore
re-ranked here by how well the title fits the request (see ``_fred_rank_key``).
"""

from __future__ import annotations

import pandas as pd

from econ_gateway.consts import FRED_FALLBACK_MIN_CATALOG_HITS, FRED_SEARCH_CANDIDATES
from econ_gateway.models import SearchHit, SearchResponse
from econ_gateway.resolve.text import expand, tokenize
from econ_gateway.services.errors import GatewayError
from econ_gateway.services.metadata import NATIONAL, frequency_words
from econ_gateway.services.upstream import Upstream

# Ways people say "the whole country"; these never trigger a geography fallback.
_NATIONAL_ALIASES = {"", "us", "u.s.", "usa", "united states", "national", "nationwide", "america"}
# Title words that never narrow *what* is measured ("All Employees: Total Private in Texas").
_NEUTRAL_TITLE_TOKENS = {"employee", "total", "private", "nonfarm", "item", "msa", "state"}


def _records(frame: pd.DataFrame) -> list[dict[str, str]]:
    """Rows as plain string dicts; missing values become ""."""
    return [
        {str(k): "" if pd.isna(v) else str(v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _catalog_hits(frame: pd.DataFrame) -> list[SearchHit]:
    return [
        SearchHit(
            series_id=row["series_id"],
            source="BLS" if row["source"].upper() == "BLS" else "FRED",
            title=row["title"],
            units=row["units"] or "unknown",
            frequency=frequency_words(row["frequency"]),
            seasonal_adjustment=row["seasonal_adjustment"] or "unknown",
            geography=row["geography"] or NATIONAL,
            latest_observation=row["last_obs"] or None,
            match="catalog",
        )
        for row in _records(frame)
    ]


def _fred_rank_key(row: dict[str, str], wanted: set[str]) -> tuple[int, int, int, float]:
    """Sort key for a FRED match; lower sorts first.

    1. More of the requested words in the title (with economics synonyms).
    2. Fewer title words that were *not* requested, so "Construction" or a metro name loses
       to the statewide total when neither was asked for.
    3. Sub-annual before annual-average copies (FRED IDs ending in "A").
    4. FRED popularity.
    """
    title_tokens = set(tokenize(row["title"]))
    matched = len(wanted & title_tokens)
    extra = len(title_tokens - wanted - _NEUTRAL_TITLE_TOKENS)
    annual = int(row["frequency_short"].upper() == "A" and "annual" not in wanted)
    popularity = float(row.get("popularity") or 0)
    return (-matched, extra, annual, -popularity)


def _fred_hits(frame: pd.DataFrame, query: str, geography: str | None) -> list[SearchHit]:
    wanted = set(expand(tokenize(f"{query} {geography or ''}")))
    rows = sorted(_records(frame), key=lambda row: _fred_rank_key(row, wanted))
    return [
        SearchHit(
            series_id=row["id"],
            source="FRED",
            title=row["title"],
            units=row["units_short"] or "unknown",
            frequency=frequency_words(row["frequency_short"]),
            seasonal_adjustment=row["seasonal_adjustment_short"] or "unknown",
            # FRED search returns no geography field; report the requested place when the
            # title names it.
            geography=geography
            if geography and geography.lower() in row["title"].lower()
            else None,
            latest_observation=row["observation_end"] or None,
            match="fred_search",
        )
        for row in rows
    ]


def _fallback_reasons(
    hits: list[SearchHit], named_places: set[str], geography: str | None
) -> tuple[list[str], bool]:
    """Why FRED should be searched, and whether the reason is a geography mismatch."""
    reasons: list[str] = []
    geo_mismatch = False
    if len(hits) < FRED_FALLBACK_MIN_CATALOG_HITS:
        reasons.append(f"the catalog had only {len(hits)} match(es)")
    if named_places and not any(h.geography in named_places for h in hits):
        reasons.append(f"no catalog series cover {', '.join(sorted(named_places))}")
        geo_mismatch = True
    elif geography and not named_places and geography.strip().lower() not in _NATIONAL_ALIASES:
        reasons.append(f"the catalog has no series for '{geography}'")
        geo_mismatch = True
    return reasons, geo_mismatch


def search_series(
    query: str, upstream: Upstream, *, geography: str | None = None, limit: int
) -> SearchResponse:
    """Find candidate series for a plain-language description.

    Args:
        query: What to measure, e.g. "unemployment rate" or "average hourly earnings".
        upstream: Data access.
        geography: Optional place (state, metro, region). Also detected inside ``query``.
        limit: Maximum results.

    Returns:
        Ranked candidates. When a place was asked for but the catalog does not cover it, FRED
        matches are listed first because catalog matches would be for the wrong place.
    """
    retriever = upstream.retriever()
    catalog_frame = retriever.search(query, k=limit, geography=geography)
    hits = _catalog_hits(catalog_frame)
    named_places = retriever.detect_geographies(f"{query} {geography or ''}")
    reasons, geo_mismatch = _fallback_reasons(hits, named_places, geography)

    notes: list[str] = []
    fred_searched = False
    if reasons and not upstream.has_fred_key:
        notes.append(
            f"FRED search was skipped ({'; '.join(reasons)}) because the server has no "
            "FRED_API_KEY. Results are limited to the curated catalog."
        )
    elif reasons:
        text = f"{query} {geography or ''}".strip()
        try:
            fred_frame = upstream.search_fred(text, limit=max(FRED_SEARCH_CANDIDATES, limit))
            fred_searched = True
        except GatewayError as exc:
            notes.append(f"FRED search failed ({exc}); results are catalog-only.")
            fred_frame = pd.DataFrame()
        if fred_searched:
            notes.append(f"Also searched FRED because {'; '.join(reasons)}.")
            seen = {h.series_id for h in hits}
            fred = [h for h in _fred_hits(fred_frame, query, geography) if h.series_id not in seen]
            hits = fred + hits if geo_mismatch else hits + fred

    if not hits:
        notes.append(
            "No matches. Describe the measure in standard terms (e.g. 'unemployment rate', "
            "'average hourly earnings', 'job openings', 'consumer price index') and give the "
            "place separately in 'geography'."
        )
    return SearchResponse(
        query=query, results=hits[:limit], fred_searched=fred_searched, notes=notes
    )
