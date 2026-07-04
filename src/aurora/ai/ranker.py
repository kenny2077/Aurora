"""Optional LLM ranking and enrichment helpers."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aurora.ai.client import AIClient, AIResponseFormatError
from aurora.ai.retry import classify_ai_failure, complete_json_with_retries
from aurora.ai.scoring import combine_scores
from aurora.ai.usage import approx_tokens, record_ai_failure, record_ai_success, reserve_ai_budget
from aurora.config import AIConfig, AITask, FinalScoreWeights
from aurora.models import SignalItem
from aurora.pipeline import StageContext


class LLMAnalysis(BaseModel):
    """Strict JSON contract returned by optional LLM analysis."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(default=5.0, ge=0.0, le=10.0)
    summary: str = ""
    why_it_matters: str = ""
    learning_value: str = ""
    action_items: list[str] = Field(default_factory=list)
    suggested_learning_path: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def parse_score(cls, value: Any) -> float:
        if value is None or value == "":
            return 5.0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return 5.0
        return float(match.group(0))

    @field_validator("summary", "why_it_matters", "learning_value", "suggested_learning_path", mode="before")
    @classmethod
    def coerce_public_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    @field_validator("action_items", "tags", mode="before")
    @classmethod
    def coerce_text_list(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [
            part.strip(" \t\r\n-*")
            for part in re.split(r"[\n;,]+", str(value))
            if part.strip(" \t\r\n-*")
        ]


PromptBuilder = Callable[[SignalItem], tuple[str, str]]
PROMPT_RAW_CONTENT_CHARS = 1000
PROMPT_METADATA_KEYS = (
    "description",
    "full_name",
    "stars",
    "forks",
    "open_issues",
    "language",
    "topics",
    "license",
    "homepage",
    "updated_at",
    "created_at",
    "feed_name",
    "score",
    "descendants",
    "comment_count",
    "venue",
    "venue_year",
    "year",
    "status",
    "semantic_scholar_tldr",
    "code_urls",
    "project_urls",
    "recommendation_evidence",
    "package_files",
    "quality_label",
    "selection_reason",
)


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
                    payload = await complete_json_with_retries(
                        self.client,
                        self.config,
                        context,
                        self.task,
                        system_prompt,
                        user_prompt,
                    )
                    if payload is None:
                        return item.id, None
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
                        record_ai_failure(
                            self.config,
                            context,
                            self.task,
                            _elapsed_ms(started),
                            json_failure=True,
                            failure_category="invalid_response",
                        )
                    return item.id, None
                except Exception as exc:
                    failed_count += 1
                    async with budget_lock:
                        record_ai_failure(
                            self.config,
                            context,
                            self.task,
                            _elapsed_ms(started),
                            failure_category=classify_ai_failure(exc),
                        )
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
            "raw_content": item.raw_content[:PROMPT_RAW_CONTENT_CHARS],
            "metadata": _prompt_metadata(item.metadata),
        },
        sort_keys=True,
        default=str,
    )


def _prompt_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in PROMPT_METADATA_KEYS:
        if key not in metadata:
            continue
        compact[key] = _compact_prompt_value(metadata[key])
    return compact


def _compact_prompt_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_compact_prompt_value(item) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key): _compact_prompt_value(item)
            for key, item in list(value.items())[:12]
            if str(key).strip()
        }
    return value


def _action_items_from_suggested_path(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip(" .") for part in text.replace("\n", " ").split(".") if part.strip(" .")]
    return [part + "." for part in parts[:4]]


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)
