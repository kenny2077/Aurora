"""Display helpers for research paper metadata."""

from __future__ import annotations

import re

from aurora.models import SignalItem

MAX_DESCRIPTION_CHARS = 280


def format_paper_source_status(item: SignalItem) -> str:
    """Return a compact source label such as ``NeurIPS 2025 (Spotlight)``."""
    metadata = item.metadata
    venue = _clean_text(metadata.get("venue")) or _clean_text(item.source) or "unknown"
    year = _paper_year(metadata.get("venue_year") or metadata.get("year"))
    status = _clean_text(metadata.get("status"))

    source = venue
    if year is not None and str(year) not in source:
        source = f"{source} {year}"
    if status and status.lower() != "unknown":
        source = f"{source} ({_title_status(status)})"
    return source


def format_paper_description(item: SignalItem) -> str:
    """Return a short, student-friendly paper description."""
    metadata = item.metadata
    text = (
        _clean_text(item.summary)
        or _clean_text(metadata.get("semantic_scholar_tldr"))
        or _clean_text(item.why_it_matters)
        or _clean_text(item.raw_content)
    )
    if not text:
        title = _clean_text(item.title) or "this paper"
        return f"This paper studies {title} and why it may matter for future AI systems."
    return _short_description(text)


def _paper_year(value: object) -> int | None:
    try:
        year = int(value or 0)
    except (TypeError, ValueError):
        return None
    return year if year > 0 else None


def _clean_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.strip().split())


def _title_status(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).title()


def _short_description(value: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", value)
    selected = " ".join(sentence for sentence in sentences[:2] if sentence).strip()
    if not selected:
        selected = value
    if len(selected) <= MAX_DESCRIPTION_CHARS:
        return selected
    trimmed = selected[: MAX_DESCRIPTION_CHARS - 3].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{trimmed}..."
