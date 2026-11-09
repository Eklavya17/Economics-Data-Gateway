"""Methodology notes (``econ://methodology``): short Markdown explainers shipped with the package.

The notes live in ``econ_gateway/methodology/*.md``; the file name (without ``.md``) is the
topic. Each starts with ``# Title`` followed by a one-paragraph summary used in the index.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from econ_gateway.services.errors import InvalidRequestError

_PACKAGE = "econ_gateway.methodology"


@dataclass(frozen=True)
class Topic:
    """One methodology note.

    Attributes:
        slug: Topic name used in the URI (``seasonal-adjustment``).
        title: The note's heading.
        summary: Its first paragraph.
        text: The full Markdown.
    """

    slug: str
    title: str
    summary: str
    text: str


def _parse(slug: str, text: str) -> Topic:
    """Split a note into title, summary paragraph and body."""
    paragraphs = [p.strip() for p in text.strip().split("\n\n")]
    title = paragraphs[0].removeprefix("# ").strip()
    summary = paragraphs[1] if len(paragraphs) > 1 else ""
    return Topic(slug=slug, title=title, summary=" ".join(summary.split()), text=text)


@lru_cache(maxsize=1)
def topics() -> dict[str, Topic]:
    """All notes by slug, sorted by slug (read once per process)."""
    found: dict[str, Topic] = {}
    for entry in sorted(resources.files(_PACKAGE).iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            slug = entry.name.removesuffix(".md")
            found[slug] = _parse(slug, entry.read_text(encoding="utf-8"))
    return found


def methodology_index() -> str:
    """Markdown list of every note with its URI and summary."""
    lines = [
        "# Methodology notes",
        "",
        "How the gateway's data is produced and how to describe it correctly. Read a note at "
        "`econ://methodology/{topic}`.",
        "",
    ]
    lines += [
        f"- **{t.title}** (`econ://methodology/{t.slug}`): {t.summary}" for t in topics().values()
    ]
    return "\n".join(lines) + "\n"


def methodology_topic(topic: str) -> str:
    """The Markdown of one note.

    Raises:
        InvalidRequestError: For an unknown topic; the message lists the valid ones.
    """
    found = topics().get(topic.strip().lower())
    if found is None:
        raise InvalidRequestError(
            f"Unknown methodology topic '{topic}'. Valid topics: {', '.join(topics())}. "
            "Read econ://methodology for summaries."
        )
    return found.text
