"""Display helpers for research paper metadata."""

from __future__ import annotations

import re

from aurora.models import SignalItem


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


def _paper_year(value: object) -> int | None:
    try:
        year = int(value or 0)
    except (TypeError, ValueError):
        return None
    return year if year > 0 else None


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _title_status(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).title()
