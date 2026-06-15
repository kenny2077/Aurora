"""Shared public-copy formatting helpers."""

from __future__ import annotations

import re

from aurora.models import SignalItem


STAR_EVIDENCE_PATTERN = re.compile(r"\b\d+(?:\.\d+)?k?\s+stars\b", re.IGNORECASE)


def format_repo_value(item: SignalItem) -> str:
    """Return a public-safe repository Value string."""
    text = raw_repo_value(item)
    if is_deterministic_repo_evidence(text):
        title = str(item.metadata.get("full_name") or item.title or "This repository").strip()
        return (
            f"{title} is useful for studying its architecture, examples, and practical "
            "workflow without treating popularity metrics as the main learning reason."
        )
    return text


def raw_repo_value(item: SignalItem) -> str:
    return " ".join(str(item.why_it_matters or item.summary or item.raw_content).split())


def is_deterministic_repo_evidence(value: str) -> bool:
    lowered = value.lower()
    return "concrete learning evidence" in lowered or (
        STAR_EVIDENCE_PATTERN.search(value) is not None
        and any(term in lowered for term in ("license", "homepage", "active recently"))
    )
