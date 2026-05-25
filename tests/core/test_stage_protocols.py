from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import (
    DeduplicateStage,
    DeliverStage,
    EnrichStage,
    FetchStage,
    NormalizeStage,
    RenderStage,
    ScoreStage,
    StageContext,
    SummarizeStage,
)


def _item() -> SignalItem:
    return SignalItem(
        id="news:1",
        type="news",
        title="Signal",
        url="https://example.com/signal",
        source="example",
        published_at="2026-05-25T00:00:00Z",
    )


class DummyFetch:
    async def fetch(self, context: StageContext) -> Sequence[Any]:
        return [{"id": "raw"}]


class DummyNormalize:
    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        return [_item()]


class DummyDeduplicate:
    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        return list(items)


class DummyScore:
    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        return [ScoreResult(item_id=items[0].id, final_score=8.0)]


class DummyEnrich:
    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        return list(items)


class DummySummarize:
    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str:
        return "summary"


class DummyRender:
    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest:
        return RenderedDigest(mode=context.mode, title="Digest", markdown=summary)


class DummyDeliver:
    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        return [DeliveryResult(channel="filesystem")]


def test_dummy_classes_satisfy_stage_protocols() -> None:
    assert isinstance(DummyFetch(), FetchStage)
    assert isinstance(DummyNormalize(), NormalizeStage)
    assert isinstance(DummyDeduplicate(), DeduplicateStage)
    assert isinstance(DummyScore(), ScoreStage)
    assert isinstance(DummyEnrich(), EnrichStage)
    assert isinstance(DummySummarize(), SummarizeStage)
    assert isinstance(DummyRender(), RenderStage)
    assert isinstance(DummyDeliver(), DeliverStage)


def test_stage_protocol_methods_are_usable_without_a_runner() -> None:
    async def exercise() -> None:
        context = StageContext(mode="tech_news", run_id="test-run")
        raw = await DummyFetch().fetch(context)
        items = await DummyNormalize().normalize(raw, context)
        deduped = await DummyDeduplicate().deduplicate(items, context)
        scores = await DummyScore().score(deduped, context)
        enriched = await DummyEnrich().enrich(deduped, scores, context)
        summary = await DummySummarize().summarize(enriched, context)
        rendered = await DummyRender().render(summary, enriched, context)
        delivered = await DummyDeliver().deliver(rendered, context)

        assert summary == "summary"
        assert rendered.mode == "tech_news"
        assert delivered == [DeliveryResult(channel="filesystem")]

    asyncio.run(exercise())

