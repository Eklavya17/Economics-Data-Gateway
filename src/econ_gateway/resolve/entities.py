"""Fuzzy matching of occupations and areas to OEWS codes.

Origin: Project 1 (``labor_agent.resolve.entities``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import difflib
import re

import pandas as pd

from econ_gateway.resolve.text import expand, tokenize
from econ_gateway.sources.catalog import load_occupations, load_oews_areas

# Common plain-English names -> SOC codes (checked against the OEWS file when built).
OCC_ALIASES: dict[str, list[str]] = {
    "tech": ["15-0000"], "technology": ["15-0000"], "it": ["15-0000"], "computer": ["15-0000"],
    "software engineer": ["15-1252"], "software developer": ["15-1252"],
    "programmer": ["15-1251"], "data scientist": ["15-2051"],
    "healthcare": ["29-0000"], "health care": ["29-0000"], "medical": ["29-0000"],
    "nurse": ["29-1141"], "nurses": ["29-1141"], "registered nurse": ["29-1141"],
    "doctor": ["29-1215", "29-1216"], "physician": ["29-1215", "29-1216"],
    "teacher": ["25-0000"], "teachers": ["25-0000"],
    "truck driver": ["53-3032"], "truck drivers": ["53-3032"],
    "lawyer": ["23-1011"], "lawyers": ["23-1011"],
    "engineer": ["17-0000"], "engineers": ["17-0000"],
    "all occupations": ["00-0000"], "all workers": ["00-0000"], "overall": ["00-0000"],
    "cashier": ["41-2011"], "cashiers": ["41-2011"],
    "electrician": ["47-2111"], "electricians": ["47-2111"],
    "accountant": ["13-2011"], "accountants": ["13-2011"],
}  # fmt: skip

STATE_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip


def _score_text(query: str, title: str) -> float:
    """Phrase match (2) + share of query tokens in the title (0-1) + string similarity (0-0.5)."""
    q, t = query.lower().strip(), title.lower()
    q_tokens, t_tokens = set(expand(tokenize(q))), set(tokenize(t))
    overlap = len(q_tokens & t_tokens) / max(len(set(tokenize(q))), 1)
    phrase = 1.0 if re.search(r"\b" + re.escape(q) + r"\b", t) else 0.0
    ratio = difflib.SequenceMatcher(None, q, t).ratio()
    return 2.0 * phrase + overlap + 0.5 * ratio


def match_occupations(
    query: str, k: int = 8, occupations: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Best-matching SOC occupations with a ``score`` column, best first.

    Aliases ("nurse" → 29-1141) and exact SOC codes go to the top; broad ("major") groups get
    a small nudge for vague queries.
    """
    occ = occupations if occupations is not None else load_occupations()
    aliases = OCC_ALIASES.get(query.lower().strip(), [])
    scores = occ["occ_title"].apply(lambda t: _score_text(query, t))
    scores = scores + occ["occ_code"].isin(aliases) * 5.0 + (occ["o_group"] == "major") * 0.1
    if re.fullmatch(r"\d{2}-\d{4}", query.strip()):
        scores = scores + (occ["occ_code"] == query.strip()) * 10.0
    out = occ.assign(score=scores).sort_values(by="score", ascending=False)
    return out.head(k).reset_index(drop=True)


def match_areas(
    query: str, level: str | None = None, k: int = 8, areas: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Best-matching OEWS areas with a ``score`` column, best first.

    Args:
        query: Place name or two-letter state abbreviation.
        level: ``national``, ``state``, ``metro`` or ``None`` for any.
        k: Maximum rows.
        areas: Area table; defaults to the OEWS reference table.
    """
    table = areas if areas is not None else load_oews_areas()
    if level:
        table = table.loc[table["level"] == level]
    q = query.strip()
    if q.upper() in STATE_ABBR:
        q = STATE_ABBR[q.upper()]
    scores = table["area_title"].apply(lambda t: _score_text(q, t))
    # An exact state name must beat metros that merely contain it ("Texas" vs "..., TX").
    exact_state = (table["level"] == "state") & (table["area_title"].str.lower() == q.lower())
    scores = scores + exact_state * 5.0
    out = table.assign(score=scores).sort_values(by="score", ascending=False)
    return out.head(k).reset_index(drop=True)
