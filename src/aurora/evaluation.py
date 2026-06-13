"""Local digest quality evaluation helpers."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig
from aurora.models import SignalItem
from aurora.modes.unified_digest.render import (
    UnifiedDigestRenderer,
    UnifiedDigestSummarizer,
    select_items,
)
from aurora.pipeline import StageContext
from aurora.storage.jsonl import read_jsonl


def replay_fixture(config: AuroraConfig, fixture_path: Path) -> dict[str, Any]:
    """Replay a saved SignalItem fixture through unified digest selection/rendering."""
    items = [SignalItem.model_validate(row) for row in read_jsonl(fixture_path)]
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
