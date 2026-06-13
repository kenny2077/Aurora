"""Optional LLM ranking and enrichment helpers."""

from __future__ import annotations

import asyncio
import json
import math
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
                prompt_tokens = _approx_tokens(system_prompt) + _approx_tokens(user_prompt)
                async with budget_lock:
                    if not _reserve_ai_budget(self.config, context, prompt_tokens):
                        return item.id, None
                try:
                    payload = await self.client.complete_json(system_prompt, user_prompt)
                    async with budget_lock:
                        _record_ai_success(context, _approx_tokens(json.dumps(payload, default=str)))
                    return item.id, LLMAnalysis.model_validate(payload)
                except Exception:
                    failed_count += 1
                    async with budget_lock:
                        _record_ai_failure(context)
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


def _reserve_ai_budget(config: AIConfig, context: StageContext, prompt_tokens: int) -> bool:
    usage = _ai_usage(context)
    usage["requested_calls"] += 1
    request_limit = config.max_requests_per_run
    token_limit = config.max_tokens_per_run
    reserved_before_this_call = usage["requested_calls"] - usage["skipped_by_budget"] - 1
    over_request_limit = request_limit is not None and reserved_before_this_call >= request_limit
    over_token_limit = token_limit is not None and (
        usage["approx_total_tokens"] + prompt_tokens
    ) > token_limit
    if over_request_limit or over_token_limit:
        usage["skipped_by_budget"] += 1
        _warn_budget_exhausted(context)
        if config.fail_open_on_budget_exceeded:
            return False
        raise RuntimeError("AI budget exhausted")
    usage["approx_prompt_tokens"] += prompt_tokens
    usage["approx_total_tokens"] += prompt_tokens
    return True


def _record_ai_success(context: StageContext, completion_tokens: int) -> None:
    usage = _ai_usage(context)
    usage["succeeded_calls"] += 1
    usage["approx_completion_tokens"] += completion_tokens
    usage["approx_total_tokens"] += completion_tokens


def _record_ai_failure(context: StageContext) -> None:
    usage = _ai_usage(context)
    usage["failed_calls"] += 1


def _ai_usage(context: StageContext) -> dict[str, int]:
    usage = context.metadata.setdefault(
        "ai_usage",
        {
            "requested_calls": 0,
            "succeeded_calls": 0,
            "failed_calls": 0,
            "skipped_by_budget": 0,
            "approx_prompt_tokens": 0,
            "approx_completion_tokens": 0,
            "approx_total_tokens": 0,
        },
    )
    if not isinstance(usage, dict):
        usage = {}
        context.metadata["ai_usage"] = usage
    for key in (
        "requested_calls",
        "succeeded_calls",
        "failed_calls",
        "skipped_by_budget",
        "approx_prompt_tokens",
        "approx_completion_tokens",
        "approx_total_tokens",
    ):
        try:
            usage[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    return usage


def _warn_budget_exhausted(context: StageContext) -> None:
    warning = "AI budget exhausted; remaining items use deterministic scoring."
    warnings = context.metadata.setdefault("warnings", [])
    if isinstance(warnings, list) and warning not in warnings:
        warnings.append(warning)


def _approx_tokens(value: str) -> int:
    text = str(value or "")
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
