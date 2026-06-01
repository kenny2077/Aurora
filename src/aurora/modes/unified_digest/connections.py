"""Deterministic cross-mode connection helpers for unified_digest."""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import combinations
from typing import Any
from urllib.parse import urlsplit

from aurora.models import SignalItem


THEMES = {
    "agents",
    "mcp",
    "cv",
    "nlp",
    "rl",
    "mlops",
    "workflow-automation",
    "ml",
    "alignment",
    "multimodal",
    "devtools",
}


def build_connections(items: Sequence[SignalItem], *, limit: int = 5) -> list[dict[str, Any]]:
    """Build stable deterministic connections from selected digest items."""
    candidates: list[tuple[float, dict[str, Any]]] = []
    for first, second in combinations(items, 2):
        if first.type == second.type:
            continue
        connection = _connection(first, second)
        if connection is None:
            continue
        candidates.append((_priority(first, second, connection), connection))
    candidates.sort(
        key=lambda pair: (
            pair[0],
            pair[1]["theme"],
            pair[1]["item_ids"],
        ),
        reverse=True,
    )
    return [connection for _, connection in candidates[:limit]]


def _connection(first: SignalItem, second: SignalItem) -> dict[str, Any] | None:
    shared_repos = sorted(_repo_slugs(first).intersection(_repo_slugs(second)))
    shared_tags = sorted(_item_terms(first).intersection(_item_terms(second)))
    evidence = _evidence_terms(shared_tags, shared_repos)
    if not evidence:
        return None

    reasons: list[str] = []
    if shared_repos:
        reasons.append(f"shared repository {shared_repos[0]}")
    if shared_tags:
        reasons.append(f"shared tags: {', '.join(shared_tags[:4])}")
    if not reasons:
        return None
    return {
        "theme": _theme(evidence),
        "item_ids": [first.id, second.id],
        "evidence_terms": evidence,
        "reason": "; ".join(reasons),
    }


def _priority(first: SignalItem, second: SignalItem, connection: dict[str, Any]) -> float:
    reason = str(connection["reason"])
    repo_bonus = 2.0 if "shared repository" in reason else 0.0
    tag_bonus = 0.5 if "shared tags" in reason else 0.0
    return repo_bonus + tag_bonus + ((_item_score(first) + _item_score(second)) / 20.0)


def _evidence_terms(shared_tags: list[str], shared_repos: list[str]) -> list[str]:
    evidence = [*shared_tags[:4], *shared_repos[:2]]
    return sorted(dict.fromkeys(evidence))


def _theme(evidence_terms: list[str]) -> str:
    for term in evidence_terms:
        if term in THEMES:
            return term
    for term in evidence_terms:
        for theme in THEMES:
            if theme in term:
                return theme
    return evidence_terms[0] if evidence_terms else "related"


def _repo_slugs(item: SignalItem) -> set[str]:
    candidates: list[str] = [str(item.url), item.raw_content, item.title]
    metadata = item.metadata
    for key in ("full_name", "repository", "repo", "homepage", "readme_url", "html_url"):
        value = metadata.get(key)
        if value:
            candidates.append(str(value))
    for key in ("code_urls", "project_urls", "repo_urls"):
        value = metadata.get(key)
        if isinstance(value, list):
            candidates.extend(str(entry) for entry in value)
        elif value:
            candidates.append(str(value))

    slugs: set[str] = set()
    for candidate in candidates:
        slugs.update(_extract_repo_slugs(candidate))
    return slugs


def _extract_repo_slugs(value: str) -> set[str]:
    slugs: set[str] = set()
    text = value.strip()
    if not text:
        return slugs
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        slugs.add(_clean_repo_slug(text))
    for match in re.finditer(r"github\.com[:/]+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text):
        slugs.add(_clean_repo_slug(match.group(1)))
    parsed = urlsplit(text)
    if parsed.netloc.lower().endswith("github.com"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            slugs.add(_clean_repo_slug(f"{parts[0]}/{parts[1]}"))
    return slugs


def _clean_repo_slug(value: str) -> str:
    slug = value.strip().strip("/").lower()
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug


def _item_terms(item: SignalItem) -> set[str]:
    values: list[Any] = [*item.tags]
    metadata = item.metadata
    for key in ("tags", "topics", "categories", "fields", "interests"):
        metadata_value = metadata.get(key)
        if isinstance(metadata_value, list):
            values.extend(metadata_value)
        elif metadata_value:
            values.append(metadata_value)
    explicit_terms = {_normalize_tag(value) for value in values if _normalize_tag(value)}
    item_text = _normalize_tag(f"{item.title} {item.raw_content}")
    theme_matches = {theme for theme in THEMES if theme in item_text}
    return explicit_terms.union(theme_matches)


def _normalize_tag(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _item_score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score or 0.0
