"""Deterministic grading of free-text answers against oracle expectations.

The grader is lenient about *format* and strict about *facts* (the Anthropic eval guidance:
don't fail correct answers over formatting):

- **Numbers** are pulled out of the answer with their scale words, so "$95,970", "95970",
  "159.3 million" and "931K" are all understood. An expected value matches if any number in the
  answer is within the tolerance. Alternatives cover different units (thousands vs. persons).
- **Periods** match any common spelling: "September 2026", "Sep. 2026", "2026-09", "Q1 2026",
  "first quarter of 2026", "November 12".
- **Keywords** are case-insensitive substrings; each group needs one hit.
- **Abstention** cases pass only if the answer says the data is not available.

No LLM judge: every check is reproducible and costs nothing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_SCALES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}  # fmt: skip
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-−–])?\$?\s?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?:\s?(?P<scale>thousand|million|billion|mn|bn|[kKmMbB])(?![a-zA-Z]))?"
)
ABSTAIN_PHRASES = (
    "not available", "not yet available", "not yet been released", "hasn't been released",
    "has not been released", "not been published", "not yet published", "isn't available",
    "is not available", "no data", "not published", "unavailable", "in the future",
    "hasn't happened", "has not happened", "not yet occurred", "cannot provide",
    "can't provide", "don't have data", "do not have data", "not been released",
    "won't be available", "will not be available", "not yet released", "not released yet",
)  # fmt: skip


def extract_numbers(text: str) -> list[float]:
    """Every number in ``text``, plus its scaled value when a scale word follows it."""
    values: list[float] = []
    for match in _NUMBER.finditer(text):
        value = float(match.group("int").replace(",", "") + (match.group("dec") or ""))
        if match.group("sign"):
            value = -value
        values.append(value)
        scale = match.group("scale")
        if scale:
            values.append(value * _SCALES[scale.lower()])
    return values


@dataclass
class Expect:
    """A number the answer must contain.

    Attributes:
        name: What the number is (for reports).
        values: Acceptable values (e.g. the same figure in thousands and in persons).
        abs_tol: Absolute tolerance.
        rel_tol: Relative tolerance (fraction of the expected value).
        sign_free: Compare magnitudes only, for changes that answers often phrase as
            "fell 0.2 points" rather than "-0.2".
    """

    name: str
    values: list[float]
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    sign_free: bool = False

    def matches(self, candidates: list[float]) -> bool:
        """Whether any candidate is within tolerance of any acceptable value."""
        for expected in self.values:
            if expected is None or math.isnan(expected):
                continue
            tol = max(self.abs_tol, self.rel_tol * abs(expected), 1e-9)
            for got in candidates:
                a, b = (abs(got), abs(expected)) if self.sign_free else (got, expected)
                if abs(a - b) <= tol:
                    return True
        return False


@dataclass
class Expectation:
    """What a correct answer contains, as computed by a case's oracle.

    Attributes:
        numbers: Numbers that must all appear.
        periods: Groups of period spellings; each group needs one match.
        keywords: Groups of keywords; each group needs one match.
        abstain: The correct answer says the data is not available.
        truth: Human-readable statement of the right answer (for reports).
    """

    numbers: list[Expect] = field(default_factory=list)
    periods: list[list[str]] = field(default_factory=list)
    keywords: list[list[str]] = field(default_factory=list)
    abstain: bool = False
    truth: str = ""


@dataclass
class Check:
    """One graded criterion."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class Grade:
    """Result of grading one answer.

    Attributes:
        checks: Every criterion with its outcome.
    """

    checks: list[Check]

    @property
    def passed(self) -> bool:
        """All criteria met (and there was at least one)."""
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def score(self) -> float:
        """Fraction of criteria met."""
        return sum(c.passed for c in self.checks) / len(self.checks) if self.checks else 0.0


def _mentions(text: str, phrase: str) -> bool:
    """Case-insensitive phrase match that will not let 'November 1' match 'November 12'."""
    pattern = re.escape(phrase.lower()) + (r"(?!\d)" if phrase[-1:].isdigit() else "")
    return re.search(pattern, text.lower()) is not None


def grade(answer: str, expectation: Expectation) -> Grade:
    """Grade ``answer`` against ``expectation``."""
    candidates = extract_numbers(answer)
    checks: list[Check] = []
    for expect in expectation.numbers:
        shown = ", ".join(f"{v:g}" for v in expect.values)
        checks.append(Check(f"number: {expect.name}", expect.matches(candidates), shown))
    for group in expectation.periods:
        checks.append(
            Check("period", any(_mentions(answer, p) for p in group), " | ".join(group[:3]))
        )
    for group in expectation.keywords:
        checks.append(
            Check("keyword", any(_mentions(answer, k) for k in group), " | ".join(group[:4]))
        )
    if expectation.abstain:
        said_so = any(_mentions(answer, p) for p in ABSTAIN_PHRASES)
        checks.append(Check("abstains", said_so, "says the data is not available"))
    return Grade(checks)
