"""Markdown summary and render stages for tech_news mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.modes.tech_news.notes import display_tech_news_source
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext


class TechNewsSummarizer:
    """Generate a concise Markdown summary for tech news items."""

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        sorted_items = sorted(items, key=lambda item: item.final_score or 0.0, reverse=True)
        lines = [
            "# Aurora Tech News",
            "",
            f"Selected {len(sorted_items)} tech news item(s).",
            "",
        ]
        if not sorted_items:
            lines.append("No tech news items found.")
            return "\n".join(lines)

        for index, item in enumerate(sorted_items, start=1):
            score = item.final_score if item.final_score is not None else "?"
            lines.append(
                f"{index}. [{item.title}]({item.url}) - {score}/10 - "
                f"{display_tech_news_source(item)}"
            )
        return "\n".join(lines)


class TechNewsRenderer:
    """Render tech news Markdown into a digest payload."""

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        return RenderedDigest(
            mode="tech_news",
            title="Aurora Tech News",
            markdown=summary,
        )
