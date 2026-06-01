"""Markdown rendering for unified_digest mode."""

from __future__ import annotations

from collections.abc import Sequence

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
            return "\n".join(lines)
        lines.extend(_learning_path_lines(selected))
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
