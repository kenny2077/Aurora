"""Prompt builders for optional repo_learning LLM analysis."""

from __future__ import annotations

from aurora.ai.ranker import item_prompt_payload
from aurora.models import SignalItem


REPO_LEARNING_ANALYSIS_SYSTEM = """
You rank GitHub repositories for hands-on learning. Return only JSON with:
{"score": number, "summary": string, "why_it_matters": string, "learning_value": string, "action_items": [string]}
Action items must include practical one-day and one-week study steps.
""".strip()


def build_repo_learning_prompt(item: SignalItem) -> tuple[str, str]:
    return (
        REPO_LEARNING_ANALYSIS_SYSTEM,
        "Analyze this repository recommendation candidate:\n" + item_prompt_payload(item),
    )
