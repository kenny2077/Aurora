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
    title = _clean_text(item.title) or "this paper"
    for candidate in (item.summary, metadata.get("semantic_scholar_tldr")):
        text = _clean_text(candidate)
        if text and not _is_generic_fallback_text(text):
            return _short_description(text)
    raw_text = _clean_text(item.raw_content)
    if (
        raw_text
        and not _is_generic_fallback_text(raw_text)
        and not _is_placeholder_raw_content(raw_text, title)
    ):
        return _short_description(raw_text)
    why_text = _clean_text(item.why_it_matters)
    if why_text and not _is_generic_fallback_text(why_text):
        return _short_description(why_text)
    return f"This paper studies {title} and why it may matter for future AI systems."


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


def _is_generic_fallback_text(value: str) -> bool:
    normalized = value.lower()
    generic_phrases = (
        "relevant ml research candidate",
        "today's scholar radar",
        "today\u2019s scholar radar",
    )
    return any(phrase in normalized for phrase in generic_phrases)


def _is_placeholder_raw_content(value: str, title: str) -> bool:
    return value.strip().lower() == f"{title} content".strip().lower()
