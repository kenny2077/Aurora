"""Unified digest stages."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aurora.config import AuroraConfig, UnifiedDigestModeConfig
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.models import DeliveryResult, RenderedDigest, ScoreResult, SignalItem
from aurora.pipeline import ModePipeline, PipelineRunner, StageContext
from aurora.storage.jsonl import read_jsonl


class UnifiedFetchStage:
    """Run included mode pipelines and collect enriched SignalItems."""

    name = "unified_sources"

    def __init__(
        self,
        config: AuroraConfig,
        builders: dict[str, Callable[[AuroraConfig], ModePipeline]],
    ) -> None:
        self.config = config
        self.builders = builders

    async def fetch(self, context: StageContext) -> list[SignalItem]:
        runner = PipelineRunner(output_dir=self.config.run.output_dir)
        collected: list[SignalItem] = []
        failures: list[dict[str, str]] = []
        child_run_summaries: list[dict[str, Any]] = []
        for mode in self.config.modes.unified_digest.include_modes:
            builder = self.builders.get(mode)
            try:
                if builder is None:
                    raise ValueError(f"unified_digest included unsupported mode: {mode}")
                pipeline = _without_delivery(builder(self.config))
                sub_context = context.model_copy(
                    update={
                        "mode": mode,
                        "config": self.config,
                        "metadata": _child_metadata(context.metadata),
                    }
                )
                result = await runner.run(pipeline, sub_context)
            except Exception as exc:
                failures.append({"mode": mode, "error": str(exc)})
                continue
            run_summary = result.rendered_digest.metadata.get("run_summary")
            if isinstance(run_summary, dict):
                child_run_summaries.append(run_summary)
            for row in read_jsonl(result.output_paths["enriched"]):
                collected.append(SignalItem.model_validate(row))
        if failures:
            context.metadata.setdefault("unified_mode_failures", []).extend(failures)
        if child_run_summaries:
            context.metadata.setdefault("unified_child_run_summaries", []).extend(
                child_run_summaries
            )
        return collected


class UnifiedNormalizeStage:
    """Pass through SignalItem rows collected from included modes."""

    async def normalize(self, raw_items: Sequence[Any], context: StageContext) -> list[SignalItem]:
        return [item for item in raw_items if isinstance(item, SignalItem)]


class UnifiedDeduplicateStage:
    """Cross-mode deduplication for unified digest items."""

    def __init__(self, config: UnifiedDigestModeConfig) -> None:
        self.config = config

    async def deduplicate(
        self, items: Sequence[SignalItem], context: StageContext
    ) -> list[SignalItem]:
        if not self.config.cross_mode_clusters:
            return list(items)
        kept: list[SignalItem] = []
        seen: dict[str, int] = {}
        for item in items:
            keys = dedup_keys(item)
            duplicate_index = next((seen[key] for key in keys if key in seen), None)
            if duplicate_index is None:
                kept.append(item)
                index = len(kept) - 1
                for key in keys:
                    seen[key] = index
                continue
            preferred = _prefer_item(kept[duplicate_index], item)
            kept[duplicate_index] = preferred
            for key in dedup_keys(preferred):
                seen[key] = duplicate_index
        return kept


class UnifiedScoreStage:
    """Preserve existing item scores as unified score results."""

    async def score(self, items: Sequence[SignalItem], context: StageContext) -> list[ScoreResult]:
        return [
            ScoreResult(
                item_id=item.id,
                deterministic_score=item.deterministic_score,
                llm_score=item.llm_score,
                final_score=item.final_score,
                score_breakdown=dict(item.metadata.get("score_breakdown") or {}),
                reason=str(item.metadata.get("score_reason") or "unified digest preserved score"),
                tags=list(item.tags),
                action_items=list(item.action_items),
            )
            for item in items
        ]


class UnifiedEnrichStage:
    """Annotate selected items with unified memory signals."""

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        if context.config is None:
            return list(items)
        cutoff = (context.until or datetime.now(timezone.utc)) - timedelta(
            days=context.config.modes.repo_learning.ranking.history_lookback_days
        )
        recent_ids = RepoLearningStateStore(context.config.run.state_path).recent_signal_ids(cutoff)
        if not recent_ids:
            return list(items)
        enriched: list[SignalItem] = []
        for item in items:
            if item.id not in recent_ids:
                enriched.append(item)
                continue
            metadata = dict(item.metadata)
            metadata["recently_seen"] = True
            enriched.append(item.model_copy(update={"metadata": metadata}))
        return enriched


class UnifiedDeliveryStage:
    """Record selected repo recommendations, then delegate configured delivery."""

    def __init__(self, state_store: RepoLearningStateStore, downstream=None) -> None:
        self.state_store = state_store
        self.downstream = downstream

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        repo_ids = [
            str(repo_id)
            for repo_id in rendered.metadata.get("recommended_repo_ids", [])
            if str(repo_id).strip()
        ]
        self.state_store.mark_recommended(
            repo_ids,
            context.until or datetime.now(timezone.utc),
        )
        selected_ids = [
            str(item_id)
            for item_id in rendered.metadata.get("selected_item_ids", [])
            if str(item_id).strip()
        ]
        themes = [
            str(connection.get("theme"))
            for connection in rendered.metadata.get("connections", [])
            if isinstance(connection, dict) and str(connection.get("theme") or "").strip()
        ]
        self.state_store.mark_signals(
            selected_ids,
            themes,
            context.until or datetime.now(timezone.utc),
        )
        state_result = DeliveryResult(
            channel="repo_learning_state",
            metadata={
                "recommended_count": len(repo_ids),
                "selected_count": len(selected_ids),
                "theme_count": len(set(themes)),
                "source_mode": "unified_digest",
            },
        )
        if self.downstream is None:
            return [state_result]
        return [state_result, *(await self.downstream.deliver(rendered, context))]


class _NoDeliveryStage:
    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        return [DeliveryResult(channel="unified_collect")]


def dedup_keys(item: SignalItem) -> list[str]:
    metadata = item.metadata
    source_ids = metadata.get("source_ids") if isinstance(metadata.get("source_ids"), dict) else {}
    candidates = [
        ("url", _canonical_url(str(item.url))),
        ("title", _normalize_title(item.title)),
    ]
    if item.type == "paper":
        candidates.extend(
            [
                ("doi", source_ids.get("doi") or metadata.get("doi")),
                ("arxiv", source_ids.get("arxiv")),
                ("openreview_forum", source_ids.get("openreview_forum")),
                ("semantic_scholar", source_ids.get("semantic_scholar") or metadata.get("semantic_scholar_paper_id")),
            ]
        )
    if item.type == "repo":
        candidates.extend(
            [
                ("full_name", metadata.get("full_name")),
                ("github_id", metadata.get("github_id")),
                ("node_id", metadata.get("node_id")),
            ]
        )
    return [f"{kind}:{str(value).lower().strip()}" for kind, value in candidates if value]


def _without_delivery(pipeline: ModePipeline) -> ModePipeline:
    return ModePipeline(
        mode=pipeline.mode,
        fetch_stages=pipeline.fetch_stages,
        normalize_stage=pipeline.normalize_stage,
        deduplicate_stage=pipeline.deduplicate_stage,
        score_stage=pipeline.score_stage,
        enrich_stage=pipeline.enrich_stage,
        summarize_stage=pipeline.summarize_stage,
        render_stage=pipeline.render_stage,
        deliver_stage=_NoDeliveryStage(),
    )


def _child_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in ("skip_llm", "skip_delivery", "strict_delivery")
        if key in metadata
    }


def _prefer_item(first: SignalItem, second: SignalItem) -> SignalItem:
    first_score = first.final_score or first.deterministic_score or 0.0
    second_score = second.final_score or second.deterministic_score or 0.0
    if second_score > first_score:
        return second
    return first


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", title.lower())).strip()
