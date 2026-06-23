"""Unified digest stages."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aurora.config import AuroraConfig, UnifiedDigestModeConfig
from aurora.modes.repo_learning.state import RepoLearningStateStore
from aurora.modes.unified_digest.quality import (
    PublicCopyRepairer,
    audit_rendered_public_digest,
    public_copy_quality,
)
from aurora.modes.unified_digest.render import section_candidate_order, select_items
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
        _ensure_ai_usage(context.metadata)
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
        if child_run_summaries or failures:
            context.metadata["unified_source_health"] = _aggregate_child_source_health(
                child_run_summaries,
                failures,
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

    def __init__(self, *, client: Any | None = None) -> None:
        self.client = client

    async def enrich(
        self,
        items: Sequence[SignalItem],
        score_results: Sequence[ScoreResult],
        context: StageContext,
    ) -> list[SignalItem]:
        if context.config is None:
            return list(items)
        enriched = list(items)
        cutoff = (context.until or datetime.now(timezone.utc)) - timedelta(
            days=context.config.modes.repo_learning.ranking.history_lookback_days
        )
        recent_ids = RepoLearningStateStore(context.config.run.state_path).recent_signal_ids(cutoff)
        if recent_ids:
            annotated: list[SignalItem] = []
            for item in enriched:
                if item.id not in recent_ids:
                    annotated.append(item)
                    continue
                metadata = dict(item.metadata)
                metadata["recently_seen"] = True
                annotated.append(item.model_copy(update={"metadata": metadata}))
            enriched = annotated
        return await _apply_public_copy_quality_gate(enriched, context, client=self.client)


class UnifiedDeliveryStage:
    """Record selected repo recommendations, then delegate configured delivery."""

    def __init__(self, state_store: RepoLearningStateStore, downstream=None) -> None:
        self.state_store = state_store
        self.downstream = downstream

    async def deliver(
        self, rendered: RenderedDigest, context: StageContext
    ) -> list[DeliveryResult]:
        _enforce_public_digest_quality(rendered, context)
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


async def _apply_public_copy_quality_gate(
    items: Sequence[SignalItem],
    context: StageContext,
    *,
    client: Any | None = None,
) -> list[SignalItem]:
    if context.config is None:
        return list(items)

    config = context.config.modes.unified_digest
    selected = select_items(items, config)
    if not selected:
        return list(items)

    by_id = {item.id: item for item in items}
    selected_ids = {item.id for item in selected}
    locked_ids: list[str] = []
    repairer = PublicCopyRepairer(context.config.ai, client=client)
    diagnostics = _quality_diagnostics(context.metadata)
    diagnostics["selected_items"] += len(selected)

    for selected_item in selected:
        accepted = await _accept_or_repair_public_copy(
            selected_item,
            context,
            repairer,
            diagnostics,
        )
        if accepted is not None:
            by_id[accepted.id] = accepted
            locked_ids.append(accepted.id)
            continue

        replacement = await _find_public_copy_replacement(
            selected_item,
            items,
            config,
            context,
            repairer,
            diagnostics,
            excluded_ids=selected_ids | set(locked_ids),
        )
        if replacement is not None:
            by_id[replacement.id] = replacement
            locked_ids.append(replacement.id)
            selected_ids.add(replacement.id)
            diagnostics["replaced"] += 1
            continue

        diagnostics["unresolved"] += 1
        diagnostics["unresolved_selected"] += 1
        locked_ids.append(selected_item.id)
        _record_quality_detail(
            diagnostics,
            selected_item,
            "unresolved_selected",
            public_copy_quality(selected_item).reasons,
        )
        context.metadata.setdefault("warnings", []).append(
            f"Public copy quality gate could not repair or replace {selected_item.id}."
        )

    context.metadata["unified_selected_item_ids"] = locked_ids
    return [by_id.get(item.id, item) for item in items]


async def _accept_or_repair_public_copy(
    item: SignalItem,
    context: StageContext,
    repairer: PublicCopyRepairer,
    diagnostics: dict[str, Any],
) -> SignalItem | None:
    quality = public_copy_quality(item)
    diagnostics["checked"] += 1
    if quality.ok:
        diagnostics["accepted"] += 1
        _record_quality_detail(diagnostics, item, "accepted", [])
        return item

    diagnostics["repair_attempted"] += 1
    _record_quality_detail(diagnostics, item, "repair_requested", quality.reasons)
    polished = await repairer.repair(item, context)
    if polished is None:
        diagnostics["failed"] += 1
        _record_quality_detail(diagnostics, item, "repair_skipped_or_failed", quality.reasons)
        return None

    polished_quality = public_copy_quality(polished)
    diagnostics["checked"] += 1
    if polished_quality.ok:
        diagnostics["repaired"] += 1
        _record_quality_detail(diagnostics, item, "repaired", quality.reasons)
        return polished

    diagnostics["failed"] += 1
    _record_quality_detail(
        diagnostics,
        item,
        "repair_still_low_quality",
        polished_quality.reasons,
    )
    return None


async def _find_public_copy_replacement(
    rejected_item: SignalItem,
    items: Sequence[SignalItem],
    config: UnifiedDigestModeConfig,
    context: StageContext,
    repairer: PublicCopyRepairer,
    diagnostics: dict[str, Any],
    *,
    excluded_ids: set[str],
) -> SignalItem | None:
    diagnostics["replacement_attempted"] += 1
    for candidate in section_candidate_order(items, config, rejected_item.type):
        if candidate.id in excluded_ids:
            continue
        accepted = await _accept_or_repair_public_copy(candidate, context, repairer, diagnostics)
        if accepted is not None:
            diagnostics["replacement_succeeded"] += 1
            _record_quality_detail(
                diagnostics,
                accepted,
                f"replacement_for:{rejected_item.id}",
                [],
            )
            return accepted
    return None


def _quality_diagnostics(metadata: dict[str, Any]) -> dict[str, Any]:
    diagnostics = metadata.setdefault(
        "public_copy_quality",
        {
            "checked": 0,
            "selected_items": 0,
            "accepted": 0,
            "repair_attempted": 0,
            "repaired": 0,
            "replaced": 0,
            "failed": 0,
            "unresolved": 0,
            "unresolved_selected": 0,
            "polished": 0,
            "polish_failed": 0,
            "sanitized": 0,
            "replacement_attempted": 0,
            "replacement_succeeded": 0,
            "delivery_blocked": 0,
            "details": [],
        },
    )
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        metadata["public_copy_quality"] = diagnostics
    diagnostics.setdefault("checked", 0)
    diagnostics.setdefault("selected_items", 0)
    diagnostics.setdefault("accepted", 0)
    diagnostics.setdefault("repair_attempted", 0)
    diagnostics.setdefault("repaired", 0)
    diagnostics.setdefault("replaced", 0)
    diagnostics.setdefault("failed", 0)
    diagnostics.setdefault("unresolved", 0)
    diagnostics.setdefault("unresolved_selected", 0)
    diagnostics.setdefault("polished", 0)
    diagnostics.setdefault("polish_failed", 0)
    diagnostics.setdefault("sanitized", 0)
    diagnostics.setdefault("replacement_attempted", 0)
    diagnostics.setdefault("replacement_succeeded", 0)
    diagnostics.setdefault("delivery_blocked", 0)
    diagnostics.setdefault("details", [])
    return diagnostics


def _enforce_public_digest_quality(rendered: RenderedDigest, context: StageContext) -> None:
    web_html = str(rendered.metadata.get("web_html") or "")
    audit = audit_rendered_public_digest(rendered.markdown, web_html)
    diagnostics = _quality_diagnostics(context.metadata)
    reasons = list(audit.reasons)
    reasons.extend(_missing_required_sections(rendered, context))
    if int(diagnostics.get("unresolved") or 0) > 0:
        reasons.append("public_copy_quality_failed")
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        return
    diagnostics["delivery_blocked"] += 1
    details = diagnostics.setdefault("details", [])
    if isinstance(details, list):
        details.append(
            {
                "item_id": "rendered_digest",
                "type": "unified_digest",
                "title": rendered.title,
                "action": "delivery_blocked",
                "reasons": reasons,
            }
        )
    context.metadata.setdefault("warnings", []).append(
        "Public digest delivery blocked because rendered copy failed quality checks: "
        + ", ".join(reasons)
    )
    raise RuntimeError(
        "public digest delivery blocked by quality gate: " + ", ".join(reasons)
    )


def _missing_required_sections(rendered: RenderedDigest, context: StageContext) -> list[str]:
    if context.config is None:
        return []
    required = context.config.modes.unified_digest.minimum_section_items
    counts = rendered.metadata.get("item_counts")
    if not isinstance(counts, dict):
        counts = {}
    missing: list[str] = []
    for section, minimum in required.items():
        try:
            available = int(counts.get(section) or 0)
        except (TypeError, ValueError):
            available = 0
        if available < minimum:
            missing.append(f"insufficient required section coverage: {section}")
    return missing


def _record_quality_detail(
    diagnostics: dict[str, Any],
    item: SignalItem,
    action: str,
    reasons: Sequence[str],
) -> None:
    details = diagnostics.setdefault("details", [])
    if not isinstance(details, list):
        details = []
        diagnostics["details"] = details
    details.append(
        {
            "item_id": item.id,
            "type": item.type,
            "title": item.title,
            "action": action,
            "reasons": list(reasons),
        }
    )


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
        for key in ("skip_llm", "skip_delivery", "strict_delivery", "ai_usage")
        if key in metadata
    }


def _aggregate_child_source_health(
    child_summaries: Sequence[dict[str, Any]], mode_failures: Sequence[dict[str, str]] = ()
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    totals = {"total": 0, "ok": 0, "failed": 0, "rate_limited": 0}
    for summary in child_summaries:
        mode = str(summary.get("mode") or "unknown").strip() or "unknown"
        child_sources = summary.get("sources")
        if not isinstance(child_sources, list):
            continue
        for source in child_sources:
            if not isinstance(source, dict):
                continue
            row = {"mode": mode, **source}
            sources.append(row)
            totals["total"] += 1
            if bool(row.get("ok")):
                totals["ok"] += 1
            else:
                totals["failed"] += 1
            if bool(row.get("rate_limited")):
                totals["rate_limited"] += 1
    for failure in mode_failures:
        mode = str(failure.get("mode") or "unknown").strip() or "unknown"
        sources.append(
            {
                "mode": mode,
                "source": mode,
                "stage": "child_pipeline",
                "ok": False,
                "failed_count": 1,
                "rate_limited": False,
                "error": str(failure.get("error") or "child pipeline failed"),
            }
        )
        totals["total"] += 1
        totals["failed"] += 1
    return {**totals, "sources": sources}


def _ensure_ai_usage(metadata: dict[str, Any]) -> None:
    usage = metadata.setdefault(
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
        metadata["ai_usage"] = {
            "requested_calls": 0,
            "succeeded_calls": 0,
            "failed_calls": 0,
            "skipped_by_budget": 0,
            "approx_prompt_tokens": 0,
            "approx_completion_tokens": 0,
            "approx_total_tokens": 0,
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
