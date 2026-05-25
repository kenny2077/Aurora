"""Markdown summary and render stages for scholar mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.config import ScholarModeConfig
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext


class ScholarSummarizer:
    """Generate a concise Markdown summary for scholar papers."""

    def __init__(self, config: ScholarModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = sorted(items, key=lambda item: item.final_score or 0.0, reverse=True)
        selected = [item for item in selected if (item.final_score or 0.0) >= self.config.score_threshold]
        selected = selected[: self.config.final_item_count]
        lines = ["# Aurora Scholar", "", f"Selected {len(selected)} research paper(s).", ""]
        if not selected:
            lines.append("No research papers met the scholar score threshold.")
            return "\n".join(lines)
        for index, item in enumerate(selected, start=1):
            meta = item.metadata
            authors = ", ".join(meta.get("authors") or []) or "unknown authors"
            venue = meta.get("venue") or item.source
            status = meta.get("status") or "unknown"
            excerpt = _excerpt(item.raw_content)
            lines.extend(
                [
                    f"## {index}. [{item.title}]({item.url}) - {item.final_score}/10",
                    "",
                    f"- Source: {item.source}",
                    f"- Venue/status: {venue} / {status}",
                    f"- Authors: {authors}",
                    f"- Abstract: {excerpt}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


class ScholarRenderer:
    """Render scholar Markdown into a digest payload."""

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        return RenderedDigest(mode="scholar", title="Aurora Scholar", markdown=summary)


def _excerpt(value: str, limit: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

