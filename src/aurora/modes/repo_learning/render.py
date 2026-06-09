"""Markdown summary and render stages for repo_learning mode."""

from __future__ import annotations

from collections.abc import Sequence

from aurora.config import RepoLearningModeConfig
from aurora.models import RenderedDigest, SignalItem
from aurora.pipeline import StageContext
from aurora.presentation import render_repo_digest_html


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
            warnings = "; ".join((metadata.get("quality_warnings") or [])[:4])
            lines.extend(
                [
                    f"## {index}. [{item.title}]({item.url}) - {item.final_score}/10",
                    "",
                    f"- {_repo_stats_text(metadata)}",
                    f"- Value: {item.why_it_matters}",
                    *([f"- Watch: {warnings}"] if warnings else []),
                    f"- Study: {item.learning_value}",
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
        html, web_html = render_repo_digest_html("Aurora Repo Learning", selected, context)
        return RenderedDigest(
            mode="repo_learning",
            title="Aurora Repo Learning",
            markdown=summary,
            html=html,
            metadata={
                "recommended_repo_ids": [item.id for item in selected],
                "web_html": web_html,
            },
        )


def _selected_items(
    items: Sequence[SignalItem], config: RepoLearningModeConfig
) -> list[SignalItem]:
    selected = sorted(items, key=lambda item: item.final_score or 0.0, reverse=True)
    return selected[: config.ranking.final_item_count]


def _repo_stats_text(metadata: dict) -> str:
    return " | ".join(
        [
            f"{_format_count(metadata.get('stars'))} stars",
            f"{_format_count(metadata.get('forks'))} forks",
            f"{_format_count(metadata.get('open_issues'))} open issues",
        ]
    )


def _format_count(value: object) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number < 1000:
        return str(number)
    compact = f"{number / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{compact}k"
