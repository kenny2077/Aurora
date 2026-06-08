"""Markdown rendering for unified_digest mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.config import UnifiedDigestModeConfig
from aurora.modes.tech_news.notes import (
    build_tech_news_notes,
    display_tech_news_learning,
    display_tech_news_why,
)
from aurora.modes.unified_digest.connections import build_connections
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext
from aurora.presentation import render_unified_digest_html


SECTION_TITLES = {
    "paper": "Research Papers",
    "repo": "GitHub Repos",
    "news": "Tech News",
}


class UnifiedDigestSummarizer:
    """Create a combined Markdown digest from enriched SignalItems."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = select_items(items, self.config)
        lines = ["# Aurora Unified Digest", ""]
        if not selected:
            lines.append("No items were available for the unified digest.")
            return "\n".join(lines)
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
                lines.extend(_section_item_lines(index, item))
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
        connections = build_connections(selected)
        html, web_html = render_unified_digest_html(
            "Aurora Unified Digest",
            selected,
            context,
            connections,
            self.config.section_order,
        )
        return RenderedDigest(
            mode="unified_digest",
            title="Aurora Unified Digest",
            markdown=summary,
            html=html,
            metadata={
                "selected_item_ids": [item.id for item in selected],
                "recommended_repo_ids": [item.id for item in selected if item.type == "repo"],
                "item_counts": {
                    item_type: sum(1 for item in selected if item.type == item_type)
                    for item_type in self.config.section_order
                },
                "connections": connections,
                "web_html": web_html,
            },
        )


def select_items(
    items: Sequence[SignalItem], config: UnifiedDigestModeConfig
) -> list[SignalItem]:
    selected: list[SignalItem] = []
    for item_type in config.section_order:
        section_items = [item for item in items if item.type == item_type]
        section_items.sort(key=_item_score, reverse=True)
        limit = _section_limit(config, item_type)
        for item in section_items[:limit]:
            if len(selected) >= config.max_total_items:
                return selected
            selected.append(item)
    return selected


def _section_limit(config: UnifiedDigestModeConfig, item_type: str) -> int:
    limit = config.section_limits.get(item_type) if hasattr(config, "section_limits") else None
    return limit if limit is not None else config.max_items_per_type


def _excerpt(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _section_item_lines(index: int, item: SignalItem) -> list[str]:
    score = item.final_score or 0.0
    if item.type == "repo":
        lines = [
            f"{index}. [{item.title}]({item.url}) - {score}/10",
            f"   - Source: {item.source}",
            f"   - Why: {_why_text(item)}",
            f"   - Study: {_learning_text(item)}",
        ]
        lines.extend(_repo_signal_lines(item, indent="   "))
        return lines
    if item.type == "paper":
        metadata = item.metadata
        venue = metadata.get("venue") or item.source
        status = metadata.get("status") or "unknown"
        return [
            f"{index}. [{item.title}]({item.url}) - {score}/10",
            f"   - Venue/status: {venue} / {status}",
            f"   - Summary: {_summary_text(item)}",
            f"   - Learn: {_learning_text(item)}",
        ]
    return [
        f"{index}. [{item.title}]({item.url}) - {score}/10",
        f"   - Source: {item.source}",
        f"   - Summary: {_summary_text(item)}",
    ]


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
    child_warning_lines = _child_warning_lines(child_summaries)
    if child_warning_lines:
        lines.extend(child_warning_lines)

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


def _child_warning_lines(child_summaries: object) -> list[str]:
    if not isinstance(child_summaries, list):
        return []
    lines: list[str] = []
    for summary in child_summaries:
        if not isinstance(summary, dict):
            continue
        mode = str(summary.get("mode") or "unknown")
        warnings = summary.get("warnings")
        if not isinstance(warnings, list):
            continue
        for warning in warnings:
            text = str(warning).strip()
            if text:
                lines.append(f"{mode} warning: {text}")
    return lines


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
            f"- {connection['theme']}: [{first.title}]({first.url}) + "
            f"[{second.title}]({second.url}): {connection['reason']} "
            f"(evidence: {', '.join(connection['evidence_terms'])})."
        )
    lines.append("")
    return lines


def _top_item(items: Sequence[SignalItem], item_type: str) -> SignalItem | None:
    top_items = _top_items(items, item_type, limit=1)
    return top_items[0] if top_items else None


def _top_items(items: Sequence[SignalItem], item_type: str, *, limit: int) -> list[SignalItem]:
    matching = [item for item in items if item.type == item_type]
    matching.sort(key=_item_score, reverse=True)
    return matching[:limit]


def _learning_item_lines(item: SignalItem) -> list[str]:
    why = _why_text(item)
    learning = _learning_text(item)
    lines = [
        f"- [{item.title}]({item.url}) - {_item_score(item):.1f}/10",
        f"  - Source: {item.source}",
        f"  - Why: {why}",
        f"  - Learn: {learning}",
    ]
    lines.extend(_repo_signal_lines(item, indent="  "))
    action_items = _action_items(item)
    if action_items:
        lines.append(f"  - Action: {'; '.join(action_items)}")
    lines.append("")
    return lines


def _repo_signal_lines(item: SignalItem, *, indent: str) -> list[str]:
    if item.type != "repo":
        return []
    metadata = item.metadata
    evidence = _metadata_text_list(metadata.get("recommendation_evidence"))
    warnings = _metadata_text_list(metadata.get("quality_warnings"))
    lines: list[str] = []
    if evidence:
        lines.append(f"{indent}- Evidence: {'; '.join(evidence[:6])}")
    if warnings:
        lines.append(f"{indent}- Watch: {'; '.join(warnings[:4])}")
    return lines


def _paper_signal_lines(item: SignalItem, *, indent: str) -> list[str]:
    if item.type != "paper":
        return []
    lines: list[str] = []
    learning = _learning_text(item)
    if learning:
        lines.append(f"{indent}- Learn: {learning}")
    action_items = _action_items(item)
    if action_items:
        lines.append(f"{indent}- Action: {'; '.join(action_items)}")
    semantic_url = str(item.metadata.get("semantic_scholar_url") or "").strip()
    if semantic_url:
        lines.append(f"{indent}- Semantic Scholar: {semantic_url}")
    return lines


def _metadata_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _why_text(item: SignalItem) -> str:
    if item.type == "news":
        return display_tech_news_why(item)
    return item.why_it_matters or item.summary or _excerpt(item.raw_content, 160)


def _summary_text(item: SignalItem) -> str:
    if item.type == "news":
        return item.summary or display_tech_news_why(item)
    return item.summary or item.why_it_matters or _excerpt(item.raw_content, 180)


def _learning_text(item: SignalItem) -> str:
    if item.type == "news":
        return display_tech_news_learning(item)
    return item.learning_value or item.summary or _excerpt(item.raw_content, 160)


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
        return build_tech_news_notes(item).action_items
    return []


def _item_score(item: SignalItem) -> float:
    return item.final_score if item.final_score is not None else item.deterministic_score
