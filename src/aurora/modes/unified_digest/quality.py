"""Public copy quality checks and repair prompts for unified digests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from aurora.ai.ranker import LLMAnalysis, LLMRanker, item_prompt_payload
from aurora.config import AIConfig
from aurora.modes.scholar.display import format_paper_description
from aurora.modes.tech_news.notes import display_tech_news_summary
from aurora.models import SignalItem
from aurora.pipeline import StageContext
from aurora.public_copy import is_deterministic_repo_evidence, raw_repo_value


@dataclass(frozen=True)
class PublicCopyQuality:
    """Result of validating the public text that will be rendered."""

    ok: bool
    text: str
    reasons: list[str]


SOURCE_COVERS_PATTERN = re.compile(r"\bcovers\b.{0,180}\bwith\b", re.IGNORECASE)
MARKDOWN_HEADING_PATTERN = re.compile(r"(^|\s)#{1,6}\s+\S")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\([^)]*$")


def public_copy_quality(item: SignalItem) -> PublicCopyQuality:
    """Validate the text visible for one selected digest item."""
    text = _public_text(item)
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    reasons: list[str] = []

    if len(normalized) < 20:
        reasons.append("too_short")
    if SOURCE_COVERS_PATTERN.search(normalized):
        reasons.append("source_covers_template")
    if "updates #" in lowered or MARKDOWN_HEADING_PATTERN.search(normalized):
        reasons.append("raw_markdown")
    if MARKDOWN_LINK_PATTERN.search(normalized):
        reasons.append("broken_markdown")
    if item.type == "repo" and is_deterministic_repo_evidence(normalized):
        reasons.append("deterministic_repo_evidence")
    source_text = " ".join(
        str(value or "")
        for value in (item.summary, item.why_it_matters, item.learning_value, item.raw_content)
    ).lower()
    if (
        "relevant ml research candidate" in lowered
        or "today's scholar radar" in lowered
        or "relevant ml research candidate" in source_text
        or "today's scholar radar" in source_text
    ):
        reasons.append("generic_scholar_fallback")
    if _looks_like_truncated_raw_abstract(item, normalized):
        reasons.append("truncated_raw_abstract")

    return PublicCopyQuality(ok=not reasons, text=normalized, reasons=reasons)


class PublicCopyRepairer:
    """Repair selected public digest text through the existing bounded AI path."""

    def __init__(self, ai_config: AIConfig, *, client: Any | None = None) -> None:
        self.ranker = LLMRanker(ai_config, weights=_repair_weights(), client=client)

    async def repair(self, item: SignalItem, context: StageContext) -> SignalItem | None:
        """Return an AI-repaired item, or ``None`` when AI is unavailable/budget-skipped."""
        analyses = await self.ranker.analyze_items([item], _repair_prompt, context)
        analysis = analyses.get(item.id)
        if analysis is None:
            return None
        return apply_public_copy_repair(item, analysis)


def apply_public_copy_repair(item: SignalItem, analysis: LLMAnalysis) -> SignalItem:
    """Apply a repair response to the public fields for one item."""
    summary = _clean_public_sentence(analysis.summary)
    why = _clean_public_sentence(analysis.why_it_matters)
    learning = _clean_public_sentence(analysis.learning_value)

    if item.type == "news":
        public = summary or why or learning
        return item.model_copy(
            update={
                "summary": public or item.summary,
                "why_it_matters": public or item.why_it_matters,
            }
        )
    if item.type == "repo":
        public = why or summary or learning
        return item.model_copy(
            update={
                "why_it_matters": public or item.why_it_matters,
                "summary": summary or item.summary,
            }
        )
    public = summary or why or learning
    return item.model_copy(
        update={
            "summary": public or item.summary,
            "why_it_matters": why or item.why_it_matters,
        }
    )


def _public_text(item: SignalItem) -> str:
    if item.type == "news":
        return display_tech_news_summary(item)
    if item.type == "paper":
        return format_paper_description(item)
    return raw_repo_value(item)


def _looks_like_truncated_raw_abstract(item: SignalItem, text: str) -> bool:
    if item.type != "paper" or not text.endswith("..."):
        return False
    metadata = item.metadata
    if item.summary or metadata.get("semantic_scholar_tldr"):
        return False
    raw = " ".join(str(item.raw_content or "").split())
    if not raw:
        return False
    technical_markers = (
        "typically follow",
        "paradigm",
        "we propose",
        "we present",
        "we introduce",
        "experiments show",
        "state-of-the-art",
    )
    return raw.startswith(text[:-3].strip()[:80]) or any(
        marker in raw.lower() for marker in technical_markers
    )


def _repair_prompt(item: SignalItem) -> tuple[str, str]:
    type_instruction = {
        "news": (
            "Write one concise sentence summarizing the concrete news. Do not start with "
            "a source name, do not say 'covers', and do not include Markdown."
        ),
        "repo": (
            "Write one or two concise Value sentences explaining what a developer or "
            "student can learn from this repository. Do not list stars, forks, license, "
            "homepage, or evidence strings."
        ),
        "paper": (
            "Write one or two student-friendly sentences explaining the idea and why it "
            "could matter in practice. Avoid raw abstract phrasing and unexplained jargon."
        ),
    }.get(item.type, "Write concise public digest copy.")
    system_prompt = (
        "You repair public copy for Aurora, a daily AI learning radar. Return strict JSON "
        "with keys score, summary, why_it_matters, learning_value, action_items, "
        "suggested_learning_path, and tags. Keep public copy polished, factual, and short."
    )
    user_prompt = json.dumps(
        {
            "task": type_instruction,
            "item": json.loads(item_prompt_payload(item)),
            "bad_public_text": _public_text(item),
            "forbidden_patterns": [
                "Source covers Title, with...",
                "updates #",
                "concrete learning evidence",
                "Relevant ML research candidate",
                "raw Markdown headings",
            ],
        },
        sort_keys=True,
        default=str,
    )
    return system_prompt, user_prompt


def _clean_public_sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.strip(" \t\r\n-")


def _repair_weights():
    from aurora.config import FinalScoreWeights

    return FinalScoreWeights(deterministic=1.0, llm=0.0)
