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
        focus = _repo_focus(item.metadata)
        return (
            f"{title} is worth studying as a {focus}; compare its setup, extension points, "
            "and workflow decisions before using popularity as the deciding signal."
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


def _repo_focus(metadata: dict) -> str:
    language = str(metadata.get("language") or "").strip()
    topics = [str(topic).strip().replace("-", " ") for topic in metadata.get("topics") or []]
    topics = [topic for topic in topics if topic]
    if language and topics:
        return f"{language} {topics[0]} project"
    if language:
        return f"{language} project"
    if topics:
        return f"{topics[0]} project"
    return "hands-on learning project"
