"""Best-effort unified digest summary refinement."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from aurora.ai.client import AIClient, AIResponseFormatError
from aurora.ai.retry import classify_ai_failure, complete_json_with_retries
from aurora.ai.usage import approx_tokens, record_ai_failure, record_ai_success, reserve_ai_budget
from aurora.config import AIConfig
from aurora.models import SignalItem
from aurora.pipeline import StageContext


class SummaryResponse(BaseModel):
    """JSON contract for a single unified-digest opening sentence."""

    model_config = ConfigDict(extra="ignore")

    summary: str


class UnifiedSummaryRefiner:
    """Generate an optional concise introduction for the deterministic digest."""

    def __init__(self, config: AIConfig, *, client: Any | None = None) -> None:
        self.config = config.model_copy(update={"model": config.model_for_task("summary")})
        self.client = client or AIClient(self.config)

    async def refine(self, items: Sequence[SignalItem], context: StageContext) -> str:
        if context.metadata.get("skip_llm") or not items or not self.client.is_configured():
            return ""
        system_prompt = (
            "Return JSON with one field named summary. Write one concise factual opening sentence "
            "for a daily AI learning digest using only the supplied items. Do not use Markdown, "
            "do not use the generic phrase 'this digest covers ... with ...', and end with a "
            "complete sentence."
        )
        user_prompt = json.dumps(
            [
                {
                    "type": item.type,
                    "title": item.title,
                    "summary": item.summary,
                    "why_it_matters": item.why_it_matters,
                }
                for item in items
            ],
            default=str,
        )
        prompt_tokens = approx_tokens(system_prompt) + approx_tokens(user_prompt)
        try:
            if not reserve_ai_budget(self.config, context, prompt_tokens, "summary"):
                return ""
        except RuntimeError:
            return ""
        started = perf_counter()
        try:
            payload = await complete_json_with_retries(
                self.client,
                self.config,
                context,
                "summary",
                system_prompt,
                user_prompt,
            )
            if payload is None:
                return ""
            response = SummaryResponse.model_validate(payload)
            summary = " ".join(response.summary.split())
            if not summary:
                raise AIResponseFormatError("AI summary response is empty")
            record_ai_success(
                self.config,
                context,
                approx_tokens(json.dumps(payload, default=str)),
                "summary",
                _elapsed_ms(started),
            )
            return summary
        except (AIResponseFormatError, ValidationError):
            record_ai_failure(
                self.config,
                context,
                "summary",
                _elapsed_ms(started),
                json_failure=True,
                failure_category="invalid_response",
            )
        except Exception as exc:
            record_ai_failure(
                self.config,
                context,
                "summary",
                _elapsed_ms(started),
                failure_category=classify_ai_failure(exc),
            )
        context.metadata.setdefault("warnings", []).append(
            "LLM summary refinement failed; deterministic digest used."
        )
        return ""


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)
