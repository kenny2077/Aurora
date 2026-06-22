"""Local digest quality evaluation helpers."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aurora.ai.ranker import LLMRanker, item_prompt_payload
from aurora.config import AuroraConfig
from aurora.models import SignalItem
from aurora.modes.unified_digest.render import (
    UnifiedDigestRenderer,
    UnifiedDigestSummarizer,
    select_items,
)
from aurora.modes.unified_digest.quality import public_copy_quality
from aurora.pipeline import StageContext
from aurora.storage.jsonl import read_jsonl


def replay_fixture(config: AuroraConfig, fixture_path: Path) -> dict[str, Any]:
    """Replay a saved SignalItem fixture through unified digest selection/rendering."""
    items = [SignalItem.model_validate(row) for row in read_jsonl(fixture_path)]
    return _replay_items(config, items, fixture_path)


def benchmark_llm_fixture(
    config: AuroraConfig,
    fixture_path: Path,
    candidate_configs: list[AuroraConfig],
    *,
    live: bool,
) -> dict[str, Any]:
    """Compare deterministic replay with explicitly configured LLM candidates."""
    items = [SignalItem.model_validate(row) for row in read_jsonl(fixture_path)]
    deterministic = _replay_items(config, items, fixture_path)
    candidates: list[dict[str, Any]] = []
    for candidate_config in candidate_configs:
        candidate = {
            "provider": candidate_config.ai.provider,
            "model": candidate_config.ai.model,
            "local_only": candidate_config.ai.local_only,
        }
        if not live:
            candidates.append(
                {
                    **candidate,
                    "status": "not_run",
                    "metrics": _empty_benchmark_metrics(candidate_config),
                }
            )
            continue
        try:
            candidates.append(
                _run_live_candidate(candidate_config, items, fixture_path, deterministic, candidate)
            )
        except Exception:
            candidates.append(
                {
                    **candidate,
                    "status": "failed",
                    "metrics": _empty_benchmark_metrics(candidate_config),
                }
            )
    return {
        "fixture": str(fixture_path),
        "live": live,
        "deterministic": deterministic,
        "candidates": candidates,
    }


def _replay_items(
    config: AuroraConfig,
    items: list[SignalItem],
    fixture_path: Path,
) -> dict[str, Any]:
    context = StageContext(
        mode="unified_digest",
        run_id=f"eval-{fixture_path.stem}",
        config=config,
    )
    summarizer = UnifiedDigestSummarizer(config.modes.unified_digest)
    renderer = UnifiedDigestRenderer(config.modes.unified_digest)
    summary = asyncio.run(summarizer.summarize(items, context))
    rendered = asyncio.run(renderer.render(summary, items, context))
    selected_ids = [str(item_id) for item_id in rendered.metadata.get("selected_item_ids") or []]
    selected = [item for item in select_items(items, config.modes.unified_digest) if item.id in set(selected_ids)]
    item_counts = _item_counts(selected, config)
    return {
        "fixture": str(fixture_path),
        "selected_item_ids": selected_ids,
        "item_counts": item_counts,
        "missing_sections": [
            item_type
            for item_type in config.modes.unified_digest.section_order
            if item_counts.get(item_type, 0) == 0
        ],
        "source_mix": _source_mix(selected, config),
        "selection_diagnostics": _selection_diagnostics(selected),
        "markdown": rendered.markdown,
    }


def _run_live_candidate(
    config: AuroraConfig,
    items: list[SignalItem],
    fixture_path: Path,
    deterministic: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    context = StageContext(mode="unified_digest", run_id=f"eval-llm-{fixture_path.stem}", config=config)
    ranker = LLMRanker(config.ai, weights=config.pipeline.scoring.default_final_weights)
    analyses = asyncio.run(ranker.analyze_items(items, _benchmark_prompt, context))
    enriched = [ranker.apply_analysis(item, analyses.get(item.id)) for item in items]
    replay = _replay_items(config, enriched, fixture_path)
    usage = context.metadata.get("ai_usage") if isinstance(context.metadata.get("ai_usage"), dict) else {}
    selected_ids = set(replay["selected_item_ids"])
    baseline_ids = set(deterministic["selected_item_ids"])
    selected_items = [item for item in enriched if item.id in selected_ids]
    union = selected_ids | baseline_ids
    requested = _int_metric(usage, "requested_calls")
    json_failures = _int_metric(usage, "json_failures")
    status = "failed" if requested and not analyses else "ok"
    return {
        **candidate,
        "status": status,
        "metrics": {
            "selected_item_overlap": len(selected_ids & baseline_ids) / len(union) if union else 1.0,
            "json_validity_rate": (requested - json_failures) / requested if requested else None,
            "summary_quality_failures": sum(
                1 for analysis in analyses.values() if not analysis.summary.strip()
            ),
            "public_copy_quality_failures": sum(
                1 for item in selected_items if not public_copy_quality(item).ok
            ),
            "latency_ms_total": _int_metric(usage, "latency_ms_total"),
            "request_count": requested,
            "fallback_count": _int_metric(usage, "deterministic_fallbacks"),
            "estimated_cloud_cost_usd": usage.get("estimated_cloud_cost_usd"),
        },
        "replay": replay,
    }


def _benchmark_prompt(item: SignalItem) -> tuple[str, str]:
    return (
        "Return a JSON object with score, summary, why_it_matters, learning_value, action_items, and tags. "
        "Use only the supplied source content.",
        item_prompt_payload(item),
    )


def _empty_benchmark_metrics(config: AuroraConfig) -> dict[str, Any]:
    return {
        "selected_item_overlap": None,
        "json_validity_rate": None,
        "summary_quality_failures": None,
        "public_copy_quality_failures": None,
        "latency_ms_total": None,
        "request_count": 0,
        "fallback_count": 0,
        "estimated_cloud_cost_usd": 0.0 if config.ai.is_local_provider() else None,
    }


def _int_metric(usage: dict[str, Any], key: str) -> int:
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def write_eval_report(path: Path, report: dict[str, Any]) -> Path:
    """Write a stable evaluation report JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def compare_reports(before_path: Path, after_path: Path) -> dict[str, Any]:
    """Compare two evaluation reports by selected IDs and section metadata."""
    before = load_eval_report(before_path)
    after = load_eval_report(after_path)
    before_ids = [str(item_id) for item_id in before.get("selected_item_ids") or []]
    after_ids = [str(item_id) for item_id in after.get("selected_item_ids") or []]
    before_set = set(before_ids)
    after_set = set(after_ids)
    return {
        "before": str(_report_path(before_path)),
        "after": str(_report_path(after_path)),
        "added": [item_id for item_id in after_ids if item_id not in before_set],
        "removed": [item_id for item_id in before_ids if item_id not in after_set],
        "unchanged_count": len(before_set.intersection(after_set)),
        "item_count_delta": _count_delta(before.get("item_counts"), after.get("item_counts")),
        "missing_sections_before": list(before.get("missing_sections") or []),
        "missing_sections_after": list(after.get("missing_sections") or []),
        "source_mix_before": before.get("source_mix") or {},
        "source_mix_after": after.get("source_mix") or {},
    }


def load_eval_report(path: Path) -> dict[str, Any]:
    """Load an evaluation report from a JSON file or directory containing evaluation.json."""
    report_path = _report_path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"evaluation report must be a JSON object: {report_path}")
    return payload


def _report_path(path: Path) -> Path:
    if path.is_dir():
        return path / "evaluation.json"
    return path


def _item_counts(items: list[SignalItem], config: AuroraConfig) -> dict[str, int]:
    return {
        item_type: sum(1 for item in items if item.type == item_type)
        for item_type in config.modes.unified_digest.section_order
    }


def _source_mix(items: list[SignalItem], config: AuroraConfig) -> dict[str, dict[str, int]]:
    mix: dict[str, dict[str, int]] = {}
    for item_type in config.modes.unified_digest.section_order:
        counter = Counter(item.source for item in items if item.type == item_type)
        mix[item_type] = dict(sorted(counter.items()))
    return mix


def _selection_diagnostics(items: list[SignalItem]) -> dict[str, dict[str, str]]:
    diagnostics: dict[str, dict[str, str]] = {}
    for item in items:
        quality_label = str(item.metadata.get("quality_label") or "").strip()
        selection_reason = str(item.metadata.get("selection_reason") or "").strip()
        if quality_label or selection_reason:
            diagnostics[item.id] = {
                "quality_label": quality_label,
                "selection_reason": selection_reason,
            }
    return diagnostics


def _count_delta(before: object, after: object) -> dict[str, int]:
    before_counts = before if isinstance(before, dict) else {}
    after_counts = after if isinstance(after, dict) else {}
    keys = sorted({*before_counts, *after_counts})
    delta: dict[str, int] = {}
    for key in keys:
        try:
            before_value = int(before_counts.get(key) or 0)
            after_value = int(after_counts.get(key) or 0)
        except (AttributeError, TypeError, ValueError):
            before_value = 0
            after_value = 0
        delta[str(key)] = after_value - before_value
    return delta
