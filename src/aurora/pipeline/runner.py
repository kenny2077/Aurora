"""Shared Aurora pipeline runner."""

from __future__ import annotations

import json
import re
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
from aurora.storage.source_quality import update_source_quality


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

        output_paths = {
            "normalized": normalized_path,
            "deduplicated": deduplicated_path,
            "score_results": score_results_path,
            "enriched": enriched_path,
        }
        run_summary = _run_summary(
            run_id=context.run_id,
            mode=pipeline.mode,
            raw_count=len(raw_items),
            normalized_count=len(normalized_items),
            deduplicated_count=len(deduplicated_items),
            score_result_count=len(score_results),
            enriched_count=len(enriched_items),
            source_statuses=source_statuses,
            source_quality=_source_quality(context, pipeline.mode, source_statuses),
            context_metadata=context.metadata,
        )
        context.metadata["run_summary"] = run_summary

        summary = await pipeline.summarize_stage.summarize(enriched_items, context)
        rendered_digest = await pipeline.render_stage.render(summary, enriched_items, context)
        _copy_render_metadata_to_context(rendered_digest.metadata, context)
        run_summary = _run_summary(
            run_id=context.run_id,
            mode=pipeline.mode,
            raw_count=len(raw_items),
            normalized_count=len(normalized_items),
            deduplicated_count=len(deduplicated_items),
            score_result_count=len(score_results),
            enriched_count=len(enriched_items),
            source_statuses=source_statuses,
            source_quality=context.metadata.get("source_quality"),
            context_metadata=context.metadata,
        )
        context.metadata["run_summary"] = run_summary
        rendered_digest = rendered_digest.model_copy(
            update={"metadata": {**rendered_digest.metadata, "run_summary": run_summary}}
        )
        run_summary_path = run_dir / "run_summary.json"
        output_paths["run_summary"] = run_summary_path
        try:
            delivery_results = await pipeline.deliver_stage.deliver(rendered_digest, context)
        except Exception as exc:
            delivery_results = [
                DeliveryResult(
                    channel="delivery",
                    ok=False,
                    error=_redact_secret_like_text(str(exc)),
                )
            ]
            final_run_summary = _run_summary(
                run_id=context.run_id,
                mode=pipeline.mode,
                raw_count=len(raw_items),
                normalized_count=len(normalized_items),
                deduplicated_count=len(deduplicated_items),
                score_result_count=len(score_results),
                enriched_count=len(enriched_items),
                source_statuses=source_statuses,
                delivery_results=delivery_results,
                output_paths=output_paths,
                source_quality=context.metadata.get("source_quality"),
                context_metadata=context.metadata,
            )
            _write_json(run_summary_path, final_run_summary)
            raise

        final_run_summary = _run_summary(
            run_id=context.run_id,
            mode=pipeline.mode,
            raw_count=len(raw_items),
            normalized_count=len(normalized_items),
            deduplicated_count=len(deduplicated_items),
            score_result_count=len(score_results),
            enriched_count=len(enriched_items),
            source_statuses=source_statuses,
            delivery_results=delivery_results,
            output_paths=output_paths,
            source_quality=context.metadata.get("source_quality"),
            context_metadata=context.metadata,
        )
        _write_json(run_summary_path, final_run_summary)

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
            output_paths=output_paths,
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


def _run_summary(
    *,
    run_id: str,
    mode: str,
    raw_count: int,
    normalized_count: int,
    deduplicated_count: int,
    score_result_count: int,
    enriched_count: int,
    source_statuses: list[SourceStatus],
    delivery_results: list[DeliveryResult] | None = None,
    output_paths: dict[str, Path] | None = None,
    source_quality: object | None = None,
    context_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "counts": {
            "raw": raw_count,
            "normalized": normalized_count,
            "deduplicated": deduplicated_count,
            "score_results": score_result_count,
            "enriched": enriched_count,
        },
        "source_health": {
            "total": len(source_statuses),
            "ok": sum(1 for status in source_statuses if status.ok),
            "failed": sum(1 for status in source_statuses if not status.ok),
            "rate_limited": sum(1 for status in source_statuses if status.rate_limited),
        },
        "sources": [_source_status_summary(status) for status in source_statuses],
    }
    unified_source_health = _unified_source_health_summary(context_metadata)
    if unified_source_health:
        summary["source_health"] = {
            key: unified_source_health[key]
            for key in ("total", "ok", "failed", "rate_limited")
        }
        summary["sources"] = unified_source_health["sources"]
    if delivery_results is not None:
        summary["delivery_results"] = [
            result.model_dump(mode="json") for result in delivery_results
        ]
    if output_paths is not None:
        summary["output_paths"] = {
            key: str(value) for key, value in output_paths.items()
        }
    if isinstance(source_quality, dict):
        summary["source_quality"] = source_quality
    ai_usage = _ai_usage_summary(context_metadata)
    if ai_usage:
        summary["ai_usage"] = ai_usage
    public_copy_quality = _public_copy_quality_summary(context_metadata)
    if public_copy_quality:
        summary["public_copy_quality"] = public_copy_quality
    release_counts = _release_gate_counts(context_metadata)
    if release_counts:
        summary.update(release_counts)
    selected_ids = _selected_digest_ids(context_metadata)
    if selected_ids:
        summary.update(selected_ids)
    warnings = _summary_warnings(context_metadata)
    if warnings:
        summary["warnings"] = warnings
    return summary


def _copy_render_metadata_to_context(metadata: dict[str, Any], context: StageContext) -> None:
    item_counts = metadata.get("item_counts")
    if isinstance(item_counts, dict):
        context.metadata["item_counts"] = item_counts
    for key in ("selected_item_ids", "recommended_repo_ids"):
        values = metadata.get(key)
        if isinstance(values, list):
            context.metadata[key] = [str(value) for value in values if str(value).strip()]
    if context.config is not None and context.mode == "unified_digest":
        context.metadata["minimum_section_items"] = (
            context.config.modes.unified_digest.minimum_section_items
        )


def _release_gate_counts(context_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    item_counts = context_metadata.get("item_counts")
    if isinstance(item_counts, dict):
        payload["item_counts"] = {
            str(section): max(0, int(count or 0))
            for section, count in item_counts.items()
            if str(section) in {"news", "repo", "paper"}
        }
    minimums = context_metadata.get("minimum_section_items")
    if isinstance(minimums, dict):
        payload["minimum_section_items"] = {
            str(section): max(0, int(minimum or 0))
            for section, minimum in minimums.items()
            if str(section) in {"news", "repo", "paper"}
        }
    return payload


def _selected_digest_ids(context_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in ("selected_item_ids", "recommended_repo_ids"):
        values = context_metadata.get(key)
        if isinstance(values, list):
            cleaned = [str(value) for value in values if str(value).strip()]
            if cleaned:
                payload[key] = cleaned
    return payload


def _source_status_summary(status: SourceStatus) -> dict[str, Any]:
    summary = {
        key: value
        for key, value in status.model_dump(mode="json").items()
        if value is not None
    }
    if "error" in summary:
        summary["error"] = _redact_secret_like_text(str(summary["error"]))
    return summary


def _redact_secret_like_text(value: str) -> str:
    patterns = [
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)([a-z0-9_]*(?:token|key|secret|password)[a-z0-9_]*=)[^\s]+",
    ]
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted


def _summary_warnings(context_metadata: dict[str, Any] | None) -> list[str]:
    if not isinstance(context_metadata, dict):
        return []
    warnings: list[str] = []
    for key in ("warnings", "semantic_scholar_warnings"):
        raw_warnings = context_metadata.get(key)
        if not isinstance(raw_warnings, list):
            continue
        for warning in raw_warnings:
            text = _redact_secret_like_text(str(warning)).strip()
            if text and text not in warnings:
                warnings.append(text)
    child_summaries = context_metadata.get("unified_child_run_summaries")
    if isinstance(child_summaries, list):
        for summary in child_summaries:
            if not isinstance(summary, dict):
                continue
            mode = str(summary.get("mode") or "unknown").strip() or "unknown"
            raw_warnings = summary.get("warnings")
            if not isinstance(raw_warnings, list):
                continue
            for warning in raw_warnings:
                text = _redact_secret_like_text(str(warning)).strip()
                formatted = f"{mode}: {text}" if text else ""
                if formatted and formatted not in warnings:
                    warnings.append(formatted)
    return warnings


def _ai_usage_summary(context_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_metadata, dict):
        return {}
    usage = context_metadata.get("ai_usage")
    if not isinstance(usage, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "requested_calls",
        "network_attempts",
        "retried_calls",
        "succeeded_calls",
        "failed_calls",
        "skipped_by_budget",
        "approx_prompt_tokens",
        "approx_completion_tokens",
        "approx_total_tokens",
        "latency_ms_total",
        "json_failures",
        "deterministic_fallbacks",
    ):
        try:
            summary[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            summary[key] = 0
    call_count = summary["succeeded_calls"] + summary["failed_calls"]
    summary["latency_ms_average"] = round(summary["latency_ms_total"] / call_count) if call_count else 0
    provider = usage.get("provider")
    if isinstance(provider, str) and provider.strip():
        summary["provider"] = provider.strip()
    model = usage.get("model")
    if isinstance(model, str) and model.strip():
        summary["model"] = model.strip()
    endpoint_kind = usage.get("endpoint_kind")
    if endpoint_kind in {"local", "cloud"}:
        summary["endpoint_kind"] = endpoint_kind
    summary["local_only"] = bool(usage.get("local_only"))
    task_models = usage.get("task_models")
    if isinstance(task_models, dict):
        summary["task_models"] = {
            str(task): str(model)
            for task, model in task_models.items()
            if str(task) in {"ranking", "summary", "repair"} and str(model).strip()
        }
    failure_categories = usage.get("failure_categories")
    if isinstance(failure_categories, dict):
        summary["failure_categories"] = {
            str(category): max(0, int(count or 0))
            for category, count in failure_categories.items()
            if str(category).strip()
        }
    else:
        summary["failure_categories"] = {}
    cost = usage.get("estimated_cloud_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        summary["estimated_cloud_cost_usd"] = float(cost)
    else:
        summary["estimated_cloud_cost_usd"] = None
    return summary


def _unified_source_health_summary(context_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_metadata, dict):
        return {}
    value = context_metadata.get("unified_source_health")
    if not isinstance(value, dict):
        return {}
    sources = value.get("sources")
    if not isinstance(sources, list):
        return {}
    summary: dict[str, Any] = {"sources": []}
    for key in ("total", "ok", "failed", "rate_limited"):
        try:
            summary[key] = max(0, int(value.get(key) or 0))
        except (TypeError, ValueError):
            summary[key] = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        row = {str(key): value for key, value in source.items()}
        if "error" in row:
            row["error"] = _redact_secret_like_text(str(row["error"]))
        summary["sources"].append(row)
    return summary


def _public_copy_quality_summary(context_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_metadata, dict):
        return {}
    quality = context_metadata.get("public_copy_quality")
    if not isinstance(quality, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "checked",
        "selected_items",
        "accepted",
        "repair_attempted",
        "polished",
        "repaired",
        "replaced",
        "failed",
        "unresolved_selected",
        "polish_failed",
        "sanitized",
        "replacement_attempted",
        "replacement_succeeded",
        "delivery_blocked",
    ):
        try:
            summary[key] = int(quality.get(key) or 0)
        except (TypeError, ValueError):
            summary[key] = 0
    details = quality.get("details")
    if isinstance(details, list):
        summary["details"] = [
            detail for detail in details if isinstance(detail, dict)
        ][:50]
    return summary


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _source_quality(
    context: StageContext, mode: str, source_statuses: list[SourceStatus]
) -> dict[str, Any] | None:
    if context.config is None or not source_statuses:
        return None
    quality = update_source_quality(
        context.config.run.cache_dir,
        mode=mode,
        statuses=source_statuses,
    )
    context.metadata["source_quality"] = quality
    return quality
