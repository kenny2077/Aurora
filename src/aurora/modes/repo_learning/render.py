"""Markdown summary and render stages for repo_learning mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.config import RepoLearningModeConfig
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext


class RepoLearningSummarizer:
    """Generate a concise Markdown learning digest for repositories."""

    def __init__(self, config: RepoLearningModeConfig) -> None:
        self.config = config

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        selected = _selected_items(items, self.config)
        lines = ["# Aurora Repo Learning", "", f"Selected {len(selected)} GitHub repo(s).", ""]
        if not selected:
            lines.append("No repositories matched the repo learning criteria.")
            return "\n".join(lines)
        for index, item in enumerate(selected, start=1):
            metadata = item.metadata
            stars = int(metadata.get("stars") or 0)
            language = metadata.get("language") or "unknown"
            package_files = ", ".join((metadata.get("package_files") or [])[:5]) or "not enriched"
            evidence = "; ".join((metadata.get("recommendation_evidence") or [])[:6])
            warnings = "; ".join((metadata.get("quality_warnings") or [])[:4])
            lines.extend(
                [
                    f"## {index}. [{item.title}]({item.url}) - {item.final_score}/10",
                    "",
                    f"- Stars: {stars}",
                    f"- Language: {language}",
                    f"- Why: {item.why_it_matters}",
                    *([f"- Evidence: {evidence}"] if evidence else []),
                    *([f"- Watch: {warnings}"] if warnings else []),
                    f"- Study: {item.learning_value}",
                    f"- Files: {package_files}",
                    "- Actions:",
                    *[f"  - {action}" for action in item.action_items],
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


class RepoLearningRenderer:
    """Render repo learning Markdown into a digest payload."""

    def __init__(self, config: RepoLearningModeConfig) -> None:
        self.config = config

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        selected = _selected_items(items, self.config)
        return RenderedDigest(
            mode="repo_learning",
            title="Aurora Repo Learning",
            markdown=summary,
            metadata={"recommended_repo_ids": [item.id for item in selected]},
        )


def _selected_items(
    items: Sequence[SignalItem], config: RepoLearningModeConfig
) -> list[SignalItem]:
    selected = sorted(items, key=lambda item: item.final_score or 0.0, reverse=True)
    return selected[: config.ranking.final_item_count]
