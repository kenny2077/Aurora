"""Shared Aurora pipeline runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem, SourceStatus
from aurora.pipeline.context import StageContext
from aurora.pipeline.stages import (
    DeduplicateStage,
    DeliverStage,
    EnrichStage,
    FetchStage,
    NormalizeStage,
    RenderStage,
    ScoreStage,
    SummarizeStage,
)
from aurora.storage.jsonl import write_jsonl


@dataclass(frozen=True)
class ModePipeline:
    """A complete set of stages for one Aurora mode."""

    mode: str
    fetch_stages: list[FetchStage]
    normalize_stage: NormalizeStage
    deduplicate_stage: DeduplicateStage
    score_stage: ScoreStage
    enrich_stage: EnrichStage
    summarize_stage: SummarizeStage
    render_stage: RenderStage
    deliver_stage: DeliverStage

    def __post_init__(self) -> None:
        if not self.mode.strip():
            raise ValueError("mode must be a non-empty string")
        if not self.fetch_stages:
            raise ValueError("at least one fetch stage is required")


class PipelineRunResult(BaseModel):
    """Result metadata returned by PipelineRunner."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    mode: str
    raw_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    score_result_count: int = Field(ge=0)
    enriched_count: int = Field(ge=0)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    delivery_results: list[DeliveryResult] = Field(default_factory=list)
    rendered_digest: RenderedDigest
    output_paths: dict[str, Path] = Field(default_factory=dict)

    @field_validator("run_id", "mode")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value.strip()


class PipelineRunner:
    """Execute a mode pipeline in Aurora's fixed stage order."""

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else None

    async def run(self, pipeline: ModePipeline, context: StageContext) -> PipelineRunResult:
        """Run one mode pipeline and write intermediate JSONL snapshots."""
        if context.mode != pipeline.mode:
            context = context.model_copy(update={"mode": pipeline.mode})

        output_dir = self._output_dir(context)
        run_dir = output_dir / context.run_id / pipeline.mode
        raw_items: list[Any] = []
        source_statuses: list[SourceStatus] = []

        for fetch_stage in pipeline.fetch_stages:
            source = _stage_name(fetch_stage)
            try:
                fetched = list(await fetch_stage.fetch(context))
            except Exception as exc:
                source_statuses.append(
                    SourceStatus(
                        source=source,
                        stage="fetch",
                        ok=False,
                        failed_count=1,
                        error=str(exc),
                    )
                )
                continue

            raw_items.extend(fetched)
            source_statuses.append(
                SourceStatus(
                    source=source,
                    stage="fetch",
                    ok=True,
                    fetched_count=len(fetched),
                )
            )

        normalized_items = await pipeline.normalize_stage.normalize(raw_items, context)
        normalized_path = write_jsonl(run_dir / "normalized.jsonl", normalized_items)

        deduplicated_items = await pipeline.deduplicate_stage.deduplicate(
            normalized_items, context
        )
        deduplicated_path = write_jsonl(run_dir / "deduplicated.jsonl", deduplicated_items)

        score_results = await pipeline.score_stage.score(deduplicated_items, context)
        score_results_path = write_jsonl(run_dir / "score_results.jsonl", score_results)

        enriched_items = await pipeline.enrich_stage.enrich(
            deduplicated_items, score_results, context
        )
        enriched_path = write_jsonl(run_dir / "enriched.jsonl", enriched_items)

        summary = await pipeline.summarize_stage.summarize(enriched_items, context)
        rendered_digest = await pipeline.render_stage.render(summary, enriched_items, context)
        delivery_results = await pipeline.deliver_stage.deliver(rendered_digest, context)

        return PipelineRunResult(
            run_id=context.run_id,
            mode=pipeline.mode,
            raw_count=len(raw_items),
            normalized_count=len(normalized_items),
            deduplicated_count=len(deduplicated_items),
            score_result_count=len(score_results),
            enriched_count=len(enriched_items),
            source_statuses=source_statuses,
            delivery_results=delivery_results,
            rendered_digest=rendered_digest,
            output_paths={
                "normalized": normalized_path,
                "deduplicated": deduplicated_path,
                "score_results": score_results_path,
                "enriched": enriched_path,
            },
        )

    def _output_dir(self, context: StageContext) -> Path:
        if self.output_dir is not None:
            return self.output_dir
        if context.config is not None:
            return context.config.run.output_dir
        return Path("data/runs")


def _stage_name(stage: object) -> str:
    for attribute in ("source", "name"):
        value = getattr(stage, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return stage.__class__.__name__

