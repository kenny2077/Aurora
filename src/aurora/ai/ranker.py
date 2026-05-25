"""Optional LLM ranking and enrichment helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aurora.ai.client import AIClient
from aurora.ai.scoring import combine_scores
from aurora.config import AIConfig, FinalScoreWeights
from aurora.models import SignalItem
from aurora.pipeline import StageContext


class LLMAnalysis(BaseModel):
    """Strict JSON contract returned by optional LLM analysis."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(ge=0.0, le=10.0)
    summary: str = ""
    why_it_matters: str = ""
    learning_value: str = ""
    action_items: list[str] = Field(default_factory=list)


PromptBuilder = Callable[[SignalItem], tuple[str, str]]


class LLMRanker:
    """Run bounded, failure-isolated LLM analysis for SignalItems."""

    def __init__(
        self,
        config: AIConfig,
        *,
        weights: FinalScoreWeights,
        client: AIClient | None = None,
    ) -> None:
        self.config = config
        self.weights = weights
        self.client = client or AIClient(config)

    async def analyze_items(
        self,
        items: Sequence[SignalItem],
        prompt_builder: PromptBuilder,
        context: StageContext,
    ) -> dict[str, LLMAnalysis]:
        if context.metadata.get("skip_llm") or not self.client.is_configured():
            return {}
        semaphore = asyncio.Semaphore(self.config.analysis_concurrency)

        async def analyze(item: SignalItem) -> tuple[str, LLMAnalysis | None]:
            async with semaphore:
                if self.config.throttle_sec:
                    await asyncio.sleep(self.config.throttle_sec)
                try:
                    system_prompt, user_prompt = prompt_builder(item)
                    payload = await self.client.complete_json(system_prompt, user_prompt)
                    return item.id, LLMAnalysis.model_validate(payload)
                except Exception:
                    return item.id, None

        results = await asyncio.gather(*(analyze(item) for item in items))
        return {item_id: analysis for item_id, analysis in results if analysis is not None}

    def apply_analysis(self, item: SignalItem, analysis: LLMAnalysis | None) -> SignalItem:
        """Apply one analysis result, preserving deterministic fallback fields."""
        if analysis is None:
            return item.model_copy(
                update={
                    "final_score": combine_scores(item.deterministic_score, item.llm_score, self.weights),
                }
            )
        return item.model_copy(
            update={
                "llm_score": analysis.score,
                "final_score": combine_scores(item.deterministic_score, analysis.score, self.weights),
                "summary": analysis.summary or item.summary,
                "why_it_matters": analysis.why_it_matters or item.why_it_matters,
                "learning_value": analysis.learning_value or item.learning_value,
                "action_items": analysis.action_items or item.action_items,
            }
        )


def item_prompt_payload(item: SignalItem) -> str:
    """Render a compact JSON payload for mode prompts."""
    return json.dumps(
        {
            "id": item.id,
            "type": item.type,
            "title": item.title,
            "url": str(item.url),
            "source": item.source,
            "deterministic_score": item.deterministic_score,
            "final_score": item.final_score,
            "tags": item.tags,
            "raw_content": item.raw_content[:4000],
            "metadata": item.metadata,
        },
        sort_keys=True,
        default=str,
    )
