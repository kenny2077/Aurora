"""Async stage protocols for the Aurora shared pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline.context import StageContext


@runtime_checkable
class FetchStage(Protocol):
    """Fetch source-native records without normalizing them."""

    async def fetch(self, context: StageContext) -> Sequence[Any]: ...


@runtime_checkable
class NormalizeStage(Protocol):
    """Normalize source-native records into SignalItem objects."""

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]: ...


@runtime_checkable
class DeduplicateStage(Protocol):
    """Deduplicate normalized SignalItem objects."""

    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]: ...


@runtime_checkable
class ScoreStage(Protocol):
    """Score normalized SignalItem objects."""

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]: ...


@runtime_checkable
class EnrichStage(Protocol):
    """Enrich normalized items after scoring."""

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]: ...


@runtime_checkable
class SummarizeStage(Protocol):
    """Create a digest summary from enriched items."""

    async def summarize(self, items: Sequence[SignalItem], context: StageContext) -> str: ...


@runtime_checkable
class RenderStage(Protocol):
    """Render a summary and item set into a deliverable digest."""

    async def render(
        self, summary: str, items: Sequence[SignalItem], context: StageContext
    ) -> RenderedDigest: ...


@runtime_checkable
class DeliverStage(Protocol):
    """Deliver an already-rendered digest."""

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]: ...

