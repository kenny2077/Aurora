"""Optional LLM ranking and enrichment helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aurora.ai.client import AIClient, AIResponseFormatError
from aurora.ai.scoring import combine_scores
from aurora.ai.usage import approx_tokens, record_ai_failure, record_ai_success, reserve_ai_budget
from aurora.config import AIConfig, AITask, FinalScoreWeights
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
    suggested_learning_path: str = ""
    tags: list[str] = Field(default_factory=list)


PromptBuilder = Callable[[SignalItem], tuple[str, str]]


class LLMRanker:
    """Run bounded, failure-isolated LLM analysis for SignalItems."""

    def __init__(
        self,
        config: AIConfig,
        *,
        weights: FinalScoreWeights,
        client: AIClient | None = None,
        task: AITask = "ranking",
    ) -> None:
        self.task = task
        self.config = config.model_copy(update={"model": config.model_for_task(task)})
        self.weights = weights
        self.client = client or AIClient(self.config)

    async def analyze_items(
        self,
        items: Sequence[SignalItem],
        prompt_builder: PromptBuilder,
        context: StageContext,
    ) -> dict[str, LLMAnalysis]:
        context.metadata["llm_analysis_requested_count"] = len(items)
        if context.metadata.get("skip_llm"):
            context.metadata["llm_analysis_skipped_count"] = len(items)
            context.metadata.setdefault("warnings", []).append("LLM analysis skipped by --skip-llm.")
            return {}
        if not self.client.is_configured():
            context.metadata["llm_analysis_skipped_count"] = len(items)
            context.metadata.setdefault("warnings", []).append(
                f"LLM analysis skipped because {self.config.api_key_env} is not configured."
            )
            return {}
        semaphore = asyncio.Semaphore(self.config.analysis_concurrency)
        budget_lock = asyncio.Lock()
        failed_count = 0

        async def analyze(item: SignalItem) -> tuple[str, LLMAnalysis | None]:
            nonlocal failed_count
            async with semaphore:
                if self.config.throttle_sec:
                    await asyncio.sleep(self.config.throttle_sec)
                system_prompt, user_prompt = prompt_builder(item)
                prompt_tokens = approx_tokens(system_prompt) + approx_tokens(user_prompt)
                async with budget_lock:
                    if not reserve_ai_budget(self.config, context, prompt_tokens, self.task):
                        return item.id, None
                started = perf_counter()
                try:
                    payload = await self.client.complete_json(system_prompt, user_prompt)
                    analysis = LLMAnalysis.model_validate(payload)
                    async with budget_lock:
                        record_ai_success(
                            self.config,
                            context,
                            approx_tokens(json.dumps(payload, default=str)),
                            self.task,
                            _elapsed_ms(started),
                        )
                    return item.id, analysis
                except (AIResponseFormatError, ValidationError):
                    failed_count += 1
                    async with budget_lock:
                        record_ai_failure(self.config, context, self.task, _elapsed_ms(started), json_failure=True)
                    return item.id, None
                except Exception:
                    failed_count += 1
                    async with budget_lock:
                        record_ai_failure(self.config, context, self.task, _elapsed_ms(started))
                    return item.id, None

        results = await asyncio.gather(*(analyze(item) for item in items))
        analyses = {item_id: analysis for item_id, analysis in results if analysis is not None}
        context.metadata["llm_analysis_succeeded_count"] = len(analyses)
        context.metadata["llm_analysis_failed_count"] = failed_count
        if failed_count:
            context.metadata.setdefault("warnings", []).append(
                f"LLM analysis failed for {failed_count} item(s); deterministic fallback used."
            )
        return analyses

    def apply_analysis(self, item: SignalItem, analysis: LLMAnalysis | None) -> SignalItem:
        """Apply one analysis result, preserving deterministic fallback fields."""
        if analysis is None:
            return item.model_copy(
                update={
                    "final_score": combine_scores(item.deterministic_score, item.llm_score, self.weights),
                }
            )
        action_items = analysis.action_items or _action_items_from_suggested_path(
            analysis.suggested_learning_path
        )
        return item.model_copy(
            update={
                "llm_score": analysis.score,
                "final_score": combine_scores(item.deterministic_score, analysis.score, self.weights),
                "summary": analysis.summary or item.summary,
                "why_it_matters": analysis.why_it_matters or item.why_it_matters,
                "learning_value": analysis.learning_value or item.learning_value,
                "action_items": action_items or item.action_items,
                "tags": list(dict.fromkeys([*item.tags, *analysis.tags])),
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


def _action_items_from_suggested_path(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip(" .") for part in text.replace("\n", " ").split(".") if part.strip(" .")]
    return [part + "." for part in parts[:4]]


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)
