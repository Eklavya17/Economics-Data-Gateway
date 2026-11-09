"""Tokenizing, light stemming and synonym expansion shared by the retrievers.

Origin: Project 1 (``labor_agent.resolve.text``, commit bdfb586), now maintained here.
"""

from __future__ import annotations

import re

STOPWORDS = {
    "the", "and", "for", "with", "what", "which", "how", "are", "was", "were", "has", "have",
    "had", "did", "does", "this", "that", "than", "from", "into", "over", "last", "most", "much",
    "many", "about", "between", "across", "compare", "compared", "comparison", "versus", "show",
    "tell", "give", "data", "all", "of", "in", "on", "to", "by", "a", "an", "is", "it", "its",
    "be", "or", "u", "s", "us", "year", "years", "month", "months", "quarter", "quarters",
    "trend", "trends", "change", "latest", "current", "recent", "recently", "keeping", "keep",
    "up", "doing", "biggest", "highest", "lowest", "seen", "saw", "see", "per", "vs",
}  # fmt: skip

# Query word -> words that should also match. Keys and values are stemmed (see ``stem``).
SYNONYMS: dict[str, list[str]] = {
    "tech": ["information", "computer", "software", "technology"],
    "technology": ["information", "computer", "software"],
    "software": ["computer", "information"],
    "healthcare": ["health", "care"],
    "medical": ["health"],
    "salary": ["earning", "wage"],
    "salarie": ["earning", "wage"],
    "pay": ["earning", "wage"],
    "paid": ["earning", "wage"],
    "wage": ["earning"],
    "earning": ["wage"],
    "income": ["earning"],
    "compensation": ["earning", "eci"],
    "job": ["employment", "employee"],
    "employment": ["employee"],
    "headcount": ["employment"],
    "hiring": ["hire", "opening"],
    "inflation": ["cpi", "price"],
    "price": ["cpi"],
    "jobless": ["unemployment"],
    "unemployed": ["unemployment"],
    "vacancy": ["opening"],
    "vacancie": ["opening"],
    "resignation": ["quit"],
    "layoff": ["discharge"],
    "firing": ["layoff"],
    "factory": ["manufacturing"],
    "factorie": ["manufacturing"],
    "restaurant": ["food", "leisure"],
    "hotel": ["accommodation", "leisure"],
    "trucking": ["transportation"],
    "teen": ["youth"],
    "latino": ["hispanic"],
    "african": ["black"],
    "college": ["bachelor"],
    "underemployment": ["u-6"],
    "claim": ["claims", "insurance"],
    "prime": ["25-54", "age"],
    "prime-age": ["25-54", "prime", "age"],
    "25-54": ["prime", "age"],
}


def stem(token: str) -> str:
    """Strip plural endings: ``industries`` → ``industry``, ``wages`` → ``wage``."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lower-cased, stemmed tokens without stopwords; hyphenated words also yield their parts."""
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower()):
        parts = [word] + (word.split("-") if "-" in word else [])  # prime-age -> prime, age
        tokens.extend(stem(p) for p in parts if p not in STOPWORDS and len(p) > 1)
    return tokens


def expand(tokens: list[str]) -> list[str]:
    """``tokens`` plus their synonyms."""
    out = list(tokens)
    for token in tokens:
        out.extend(stem(s) for s in SYNONYMS.get(token, []))
    return out
