"""Markdown rendering for unified_digest mode."""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import combinations
from typing import Any
from urllib.parse import urlsplit

from aurora.config import UnifiedDigestModeConfig
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext


SECTION_TITLES = {
    "paper": "Research Papers",
    "repo": "Repositories",
    "news": "Tech News",
}


class UnifiedDigestSummarizer:
    """Create a combined Markdown digest from enriched SignalItems."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = select_items(items, self.config)
        lines = ["# Aurora Unified Digest", "", f"Selected {len(selected)} item(s).", ""]
        if not selected:
            lines.append("No items were available for the unified digest.")
            lines.extend(["", *_run_summary_lines(context)])
            return "\n".join(lines)
        lines.extend(_learning_path_lines(selected))
        lines.extend(_run_summary_lines(context))
        lines.extend(_connection_lines(selected))
        for item_type in self.config.section_order:
            section_items = [item for item in selected if item.type == item_type]
            if not section_items:
                continue
            lines.extend([f"## {SECTION_TITLES[item_type]}", ""])
            if item_type == "paper" and any(item.metadata.get("cached_fallback") for item in section_items):
                lines.extend(
                    [
                        "Using cached scholar results because live sources returned no papers.",
                        "",
                    ]
                )
            for index, item in enumerate(section_items, start=1):
                why = item.why_it_matters or item.summary or _excerpt(item.raw_content, 160)
                lines.extend(
                    [
                        f"{index}. [{item.title}]({item.url}) - {item.final_score or 0.0}/10",
                        f"   - Source: {item.source}",
                        f"   - Why: {why}",
                    ]
                )
            lines.append("")
        return "\n".join(lines).rstrip()


class UnifiedDigestRenderer:
    """Render unified Markdown into a digest payload."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        selected = select_items(items, self.config)
        return RenderedDigest(
            mode="unified_digest",
            title="Aurora Unified Digest",
            markdown=summary,
            metadata={
                "selected_item_ids": [item.id for item in selected],
                "recommended_repo_ids": [item.id for item in selected if item.type == "repo"],
                "item_counts": {
                    item_type: sum(1 for item in selected if item.type == item_type)
                    for item_type in self.config.section_order
                },
                "connections": build_connections(selected),
            },
        )


def select_items(
    items: Sequence[SignalItem], config: UnifiedDigestModeConfig
) -> list[SignalItem]:
    selected: list[SignalItem] = []
    for item_type in config.section_order:
        section_items = [item for item in items if item.type == item_type]
        section_items.sort(key=_item_score, reverse=True)
        for item in section_items[: config.max_items_per_type]:
            if len(selected) >= config.max_total_items:
                return selected
            selected.append(item)
    return selected


def _excerpt(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _learning_path_lines(items: Sequence[SignalItem]) -> list[str]:
    paper = _top_item(items, "paper")
    repo = _top_item(items, "repo")
    news_items = _top_items(items, "news", limit=3)
    lines = ["## Today's Learning Path", ""]

    lines.extend(["### Paper to Understand", ""])
    if paper is None:
        lines.extend(["No paper candidate is available for today's learning path.", ""])
    else:
        lines.extend(_learning_item_lines(paper))

    lines.extend(["### Repo to Study", ""])
    if repo is None:
        lines.extend(["No repository candidate is available for today's learning path.", ""])
    else:
        lines.extend(_learning_item_lines(repo))

    lines.extend(["### News to Watch", ""])
    if not news_items:
        lines.extend(["No news item is available for today's learning path.", ""])
    else:
        for item in news_items:
            lines.extend(_learning_item_lines(item))

    return lines


def _run_summary_lines(context: StageContext) -> list[str]:
    run_summary = context.metadata.get("run_summary")
    child_summaries = context.metadata.get("unified_child_run_summaries")
    mode_failures = context.metadata.get("unified_mode_failures")
    if not isinstance(run_summary, dict) and not child_summaries and not mode_failures:
        return []

    lines = ["## Run Summary", ""]
    if isinstance(run_summary, dict):
        counts = run_summary.get("counts")
        if isinstance(counts, dict):
            lines.append(
                "Items: "
                f"{int(counts.get('raw') or 0)} raw -> "
                f"{int(counts.get('normalized') or 0)} normalized -> "
                f"{int(counts.get('deduplicated') or 0)} deduplicated -> "
                f"{int(counts.get('enriched') or 0)} enriched."
            )
        health = run_summary.get("source_health")
        if isinstance(health, dict):
            lines.append(
                "Sources: "
                f"{int(health.get('ok') or 0)} ok, "
                f"{int(health.get('failed') or 0)} failed, "
                f"{int(health.get('rate_limited') or 0)} rate limited."
            )
        source_lines = _failed_source_lines(run_summary.get("sources"))
        if source_lines:
            lines.extend(source_lines)

    child_line = _child_mode_summary_line(child_summaries)
    if child_line:
        lines.append(child_line)

    if isinstance(mode_failures, list):
        for failure in mode_failures:
            if not isinstance(failure, dict):
                continue
            mode = str(failure.get("mode") or "unknown")
            error = str(failure.get("error") or "unknown error")
            lines.append(f"Mode {mode} failed: {error}")

    lines.append("")
    return lines


def _failed_source_lines(sources: object) -> list[str]:
    if not isinstance(sources, list):
        return []
    lines: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or source.get("ok", True):
            continue
        name = str(source.get("source") or "unknown")
        error = str(source.get("error") or "unknown error")
        lines.append(f"{name} failed: {error}")
    return lines


def _child_mode_summary_line(child_summaries: object) -> str:
    if not isinstance(child_summaries, list):
        return ""
    parts: list[str] = []
    for summary in child_summaries:
        if not isinstance(summary, dict):
            continue
        mode = str(summary.get("mode") or "unknown")
        counts = summary.get("counts")
        enriched = int(counts.get("enriched") or 0) if isinstance(counts, dict) else 0
        parts.append(f"{mode} {enriched} item(s)")
    if not parts:
        return ""
    return f"Child modes: {', '.join(parts)}."


def _connection_lines(items: Sequence[SignalItem]) -> list[str]:
    connections = build_connections(items)
    if not connections:
        return []
    lines = ["## Connections", ""]
    by_id = {item.id: item for item in items}
    for connection in connections:
        item_ids = connection["item_ids"]
        connected_items = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        if len(connected_items) != 2:
            continue
        first, second = connected_items
        lines.append(
            f"- [{first.title}]({first.url}) + [{second.title}]({second.url}): "
            f"{connection['reason']}."
        )
    lines.append("")
    return lines


def build_connections(items: Sequence[SignalItem], *, limit: int = 5) -> list[dict[str, Any]]:
    """Build deterministic cross-mode connections for selected digest items."""
    connections: list[tuple[float, dict[str, Any]]] = []
    for first, second in combinations(items, 2):
        if first.type == second.type:
            continue
        reason = _connection_reason(first, second)
        if not reason:
            continue
        priority = _connection_priority(first, second, reason)
        connections.append(
            (
                priority,
                {
                    "item_ids": [first.id, second.id],
                    "types": [first.type, second.type],
                    "reason": reason,
                },
            )
        )
    connections.sort(
        key=lambda pair: (
            pair[0],
            _item_score_by_id(items, pair[1]["item_ids"][0]),
            pair[1]["item_ids"],
        ),
        reverse=True,
    )
    return [connection for _, connection in connections[:limit]]


def _connection_reason(first: SignalItem, second: SignalItem) -> str:
    reasons: list[str] = []
    shared_repos = sorted(_repo_slugs(first).intersection(_repo_slugs(second)))
    if shared_repos:
        reasons.append(f"shared repository {shared_repos[0]}")
    shared_tags = sorted(_item_tags(first).intersection(_item_tags(second)))
    if shared_tags:
        reasons.append(f"shared tags: {', '.join(shared_tags[:4])}")
    return "; ".join(reasons)


def _connection_priority(first: SignalItem, second: SignalItem, reason: str) -> float:
    repo_bonus = 2.0 if "shared repository" in reason else 0.0
    tag_bonus = 0.5 if "shared tags" in reason else 0.0
    return repo_bonus + tag_bonus + ((_item_score(first) + _item_score(second)) / 20.0)


def _item_score_by_id(items: Sequence[SignalItem], item_id: str) -> float:
    for item in items:
        if item.id == item_id:
            return _item_score(item)
    return 0.0


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
        slugs.add(text.lower())
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


def _item_tags(item: SignalItem) -> set[str]:
    values: list[Any] = [*item.tags]
    for key in ("tags", "topics", "categories", "fields", "interests"):
        metadata_value = item.metadata.get(key)
        if isinstance(metadata_value, list):
            values.extend(metadata_value)
        elif metadata_value:
            values.append(metadata_value)
    return {_normalize_tag(value) for value in values if _normalize_tag(value)}


def _normalize_tag(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _top_item(items: Sequence[SignalItem], item_type: str) -> SignalItem | None:
    top_items = _top_items(items, item_type, limit=1)
    return top_items[0] if top_items else None


def _top_items(items: Sequence[SignalItem], item_type: str, *, limit: int) -> list[SignalItem]:
    matching = [item for item in items if item.type == item_type]
    matching.sort(key=_item_score, reverse=True)
    return matching[:limit]


def _learning_item_lines(item: SignalItem) -> list[str]:
    why = item.why_it_matters or item.summary or _excerpt(item.raw_content, 160)
    learning = item.learning_value or item.summary or _excerpt(item.raw_content, 160)
    lines = [
        f"- [{item.title}]({item.url}) - {_item_score(item):.1f}/10",
        f"  - Source: {item.source}",
        f"  - Why: {why}",
        f"  - Learn: {learning}",
    ]
    action_items = _action_items(item)
    if action_items:
        lines.append(f"  - Action: {'; '.join(action_items)}")
    lines.append("")
    return lines


def _action_items(item: SignalItem) -> list[str]:
    if item.action_items:
        return list(item.action_items)
    if item.type == "paper":
        actions = ["Read the abstract and identify the core claim."]
        if item.metadata.get("code_urls") or item.metadata.get("project_urls"):
            actions.append("Inspect the linked code or project page.")
        actions.append("Write one implementation question to investigate next.")
        return actions
    if item.type == "news":
        return [
            "Read the source or discussion thread.",
            "Identify the ecosystem impact for your current work.",
            "Decide whether this deserves a follow-up experiment.",
        ]
    return []


def _item_score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score
