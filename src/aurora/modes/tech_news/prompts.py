"""Prompt builders for optional tech_news LLM analysis."""

from __future__ import annotations

from aurora.ai.ranker import item_prompt_payload
from aurora.models import SignalItem


TECH_NEWS_ANALYSIS_SYSTEM = """
You rank timely technology news. Return only JSON with:
{"score": number, "summary": string, "why_it_matters": string, "learning_value": string, "action_items": [string], "source_credibility": string}
Favor broad impact, high engagement, and freshness over niche topic matching.
For source_credibility, give a short source/plausibility assessment such as "Likely true: primary source announcement" or "Unverified: community discussion"; do not claim full fact-checking.
""".strip()


def build_tech_news_prompt(item: SignalItem) -> tuple[str, str]:
    return (
        TECH_NEWS_ANALYSIS_SYSTEM,
        "Analyze this tech news item for a concise Aurora digest:\n" + item_prompt_payload(item),
    )
