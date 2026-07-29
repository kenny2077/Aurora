from __future__ import annotations

import asyncio

from aurora.config import AIConfig, UnifiedDigestModeConfig
from aurora.models import SignalItem
from aurora.modes.unified_digest.quality import audit_rendered_public_digest
from aurora.modes.unified_digest.render import UnifiedDigestSummarizer
from aurora.pipeline import StageContext


def test_unified_summary_refiner_uses_summary_task_model() -> None:
    config = AIConfig(
        provider="ollama",
        model="qwen2.5:3b",
        task_models={"summary": "mistral:7b"},
    )
    summarizer = UnifiedDigestSummarizer(
        UnifiedDigestModeConfig(),
        ai_config=config,
        client=_SummaryClient({"summary": "A concise opening for today's digest."}),
    )
    context = StageContext(mode="unified_digest", run_id="summary")

    summary = asyncio.run(summarizer.summarize([_item()], context))

    assert "A concise opening for today's digest." in summary
    assert context.metadata["ai_usage"]["task_models"] == {"summary": "mistral:7b"}


def test_unified_summary_refiner_keeps_deterministic_digest_on_failure() -> None:
    summarizer = UnifiedDigestSummarizer(
        UnifiedDigestModeConfig(),
        ai_config=AIConfig(provider="ollama", model="qwen2.5:3b"),
        client=_FailingSummaryClient(),
    )
    context = StageContext(mode="unified_digest", run_id="summary")

    summary = asyncio.run(summarizer.summarize([_item()], context))

    assert "## Tech News" in summary
    assert "concise opening" not in summary
    assert context.metadata["ai_usage"]["deterministic_fallbacks"] == 1


def test_unified_summary_refiner_rejects_low_quality_opening() -> None:
    bad_opening = "This digest covers agent tools, with a final comparison and."
    summarizer = UnifiedDigestSummarizer(
        UnifiedDigestModeConfig(),
        ai_config=AIConfig(provider="ollama", model="qwen2.5:3b"),
        client=_SummaryClient({"summary": bad_opening}),
    )
    context = StageContext(mode="unified_digest", run_id="summary")

    summary = asyncio.run(summarizer.summarize([_item()], context))

    assert bad_opening not in summary
    assert audit_rendered_public_digest(summary).ok
    assert context.metadata["ai_usage"]["deterministic_fallbacks"] == 1
    warning = next(
        warning
        for warning in context.metadata["warnings"]
        if "LLM summary refinement rejected by public copy quality gate" in warning
    )
    assert "source_covers_template" in warning
    assert "dangling_fragment" in warning


def _item() -> SignalItem:
    return SignalItem(
        id="news:1",
        type="news",
        title="News",
        url="https://example.com/news",
        source="test",
        published_at="2026-06-21T00:00:00Z",
        summary="A useful update.",
        deterministic_score=8.0,
        final_score=8.0,
    )


class _SummaryClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.payload


class _FailingSummaryClient:
    def is_configured(self) -> bool:
        return True

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise RuntimeError("service unavailable")
