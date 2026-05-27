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
        section_items.sort(key=lambda item: item.final_score or 0.0, reverse=True)
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
